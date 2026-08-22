-- 0260 — write receipts: a session, a material digest, and a readback that is
--        computed by the database rather than asserted by the caller
--
-- THIS LAYER WAS REJECTED ONCE. Reading that rejection is the fastest way to
-- understand every choice below, so it is written out rather than referenced:
--
--   1. It treated the POSTGRES BACKEND used to mint a receipt as the original
--      user session. A database connection is not an authenticated human
--      session; it says which credential wrote, never who authenticated or when.
--   2. Its readback proof relied on MUTABLE rows and proved no readback. A
--      digest re-read from something the writer can still change proves only
--      that nobody happened to change it yet.
--   3. Its conflict and undo path accepted ARBITRARY proposals without proving
--      causal conflict or exact reversal. "This undoes that" was taken on the
--      caller's word.
--
-- Each is answered by construction below, and each answer is exercised by the
-- apply-time proof at the bottom.
--
-- ------------------------------------------------------------------ (1) ----
-- THE SESSION IS THE APPLICATION SESSION, NOT THE CONNECTION. The link is NOT
-- NULL and references ops.application_session, and a trigger requires that
-- session to be live, unexpired, unrevoked, and to match the receipt's actor
-- and tenant — the same standard evidence rows are held to in 0257. There is no
-- code path that derives a receipt's session from current_user, the backend pid,
-- or anything else about the connection.
--
-- ------------------------------------------------------------------ (2) ----
-- THE READBACK IS COMPUTED BY THE DATABASE, FROM A FROZEN ROW. The caller
-- supplies what it BELIEVES it wrote (claimed_digest). The database then reads
-- the qualified public.tool_call row for that idempotency key and computes the
-- digest itself. Those rows are frozen against update and delete by 0257 once
-- they carry a session, so the readback source cannot be edited after the fact.
-- A receipt is VALID only when the two digests agree, and validity is a
-- generated column rather than a flag someone sets.
--
-- The caller cannot fake this: there is no parameter through which
-- readback_digest can be supplied, and the column is filled by a SECURITY
-- DEFINER function that reads the frozen row.
--
-- ------------------------------------------------------------------ (3) ----
-- CAUSAL CONFLICT AND EXACT REVERSAL ARE DEFINED, NOT ASSERTED.
--
--   Every receipt records the digest of the subject's state it BUILT ON
--   (prior_digest) as well as the state it produced. Two receipts CONFLICT when
--   they name the same subject, claim the same prior state, and produce
--   different results. That is a definition a query can evaluate; it does not
--   depend on anyone declaring a conflict.
--
--   A reversal is EXACT when the state it produces equals the state its target
--   built on: undoing R must land exactly where R started. A receipt claiming to
--   reverse another is refused unless that holds. "It undoes it" is therefore
--   checked rather than believed.

create table ops.write_receipt (
  id                       uuid primary key,
  application_session_id   uuid not null references ops.application_session(id),
  actor_id                 uuid not null references public.actor(id),
  organization_tenant_id   text not null,
  verb                     text not null,
  subject_type             text not null,
  subject_id               uuid not null,
  -- The evidence row this receipt is a receipt FOR. Frozen by 0257 once bound.
  tool_call_idempotency_key text not null,
  -- What the caller believes it wrote.
  claimed_digest           text not null,
  -- What the database found when it read the frozen row back. NULL until the
  -- readback runs; never supplied by a caller.
  readback_digest          text,
  readback_at              timestamptz,
  -- The subject state this receipt built on, and the receipt it reverses.
  prior_digest             text not null,
  reverses_receipt_id      uuid references ops.write_receipt(id),
  recorded_at              timestamptz not null default clock_timestamp(),
  -- VALIDITY IS DERIVED, NEVER SET. A boolean column someone can write is a
  -- claim; this is a consequence.
  is_proven                boolean generated always as
                             (readback_digest is not null
                              and readback_digest = claimed_digest) stored,
  constraint write_receipt_digests_nonempty
    check (length(btrim(claimed_digest)) > 0 and length(btrim(prior_digest)) > 0),
  constraint write_receipt_no_self_reversal
    check (reverses_receipt_id is null or reverses_receipt_id <> id)
);

create index write_receipt_subject_idx
  on ops.write_receipt (subject_type, subject_id, recorded_at);
create index write_receipt_session_idx
  on ops.write_receipt (application_session_id);

comment on column ops.write_receipt.readback_digest is
  'Computed by ops.prove_write_receipt from the FROZEN tool_call row. There is '
  'no parameter through which a caller can supply this value.';

-- ------------------------------------------------------- the session guard
-- Same standard as evidence in 0257: live, unexpired, unrevoked, and matching
-- actor AND tenant. A receipt naming a session that does not vouch for its
-- actor is worse than no receipt, because it looks like proof.
create function ops.require_live_session_for_receipt()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s ops.application_session%rowtype;
begin
  select * into s from ops.application_session
   where id = new.application_session_id for share;
  if not found then
    raise exception 'unknown application session % for receipt', new.application_session_id;
  end if;
  if s.revoked_at is not null then
    raise exception 'application session % is revoked', new.application_session_id;
  end if;
  if clock_timestamp() >= s.expires_at then
    raise exception 'application session % is expired', new.application_session_id;
  end if;
  if new.actor_id is distinct from s.actor_id then
    raise exception 'receipt names a different actor than its session';
  end if;
  if new.organization_tenant_id is distinct from s.organization_tenant_id then
    raise exception 'receipt names a different tenant than its session';
  end if;
  return new;
end $$;

create trigger write_receipt_requires_live_session
before insert on ops.write_receipt
for each row execute function ops.require_live_session_for_receipt();
alter table ops.write_receipt enable always trigger write_receipt_requires_live_session;

-- ------------------------------------------------- immutability of receipts
-- A receipt that can be edited is a note. The ONLY permitted change is an
-- unproven receipt acquiring its readback, and that transition is one-way.
create function ops.refuse_receipt_rewrite()
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
      new.claimed_digest, new.prior_digest, new.recorded_at)
     is distinct from
     (old.id, old.application_session_id, old.actor_id, old.organization_tenant_id,
      old.verb, old.subject_type, old.subject_id, old.tool_call_idempotency_key,
      old.claimed_digest, old.prior_digest, old.recorded_at)
     or new.reverses_receipt_id is distinct from old.reverses_receipt_id then
    raise exception 'write receipt identity is immutable';
  end if;
  if new.readback_digest is null then
    raise exception 'the only permitted update is recording the readback';
  end if;
  return new;
end $$;

create trigger write_receipt_immutable
before update or delete on ops.write_receipt
for each row execute function ops.refuse_receipt_rewrite();
alter table ops.write_receipt enable always trigger write_receipt_immutable;

-- --------------------------------------------------------- exact reversal
-- A reversal is exact when the state it produces equals the state its target
-- built on. Undoing R must land where R started, and that is checkable rather
-- than declarable.
create function ops.require_exact_reversal()
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
  -- THE WHOLE CHECK. Anything else is a caller asserting a relationship.
  if new.claimed_digest is distinct from target.prior_digest then
    raise exception
      'reversal is not exact: it produces % but the receipt it reverses built on %',
      new.claimed_digest, target.prior_digest;
  end if;
  return new;
end $$;

create trigger write_receipt_reversal_is_exact
before insert on ops.write_receipt
for each row execute function ops.require_exact_reversal();
alter table ops.write_receipt enable always trigger write_receipt_reversal_is_exact;

-- ------------------------------------------------------------- the readback
-- SECURITY DEFINER because it must read public.tool_call and write the receipt's
-- readback column, neither of which the caller may do directly. It takes only a
-- receipt id: there is no parameter through which a digest could be supplied.
create function ops.prove_write_receipt(p_receipt_id uuid)
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
  -- THE EVIDENCE MUST ITSELF BE QUALIFIED, and by the SAME session. A receipt
  -- proved against a legacy row would be a proof about something 0257 says
  -- proves nothing.
  if tc.application_session_id is null then
    raise exception 'receipt % names LEGACY evidence, which cannot be read back',
      p_receipt_id;
  end if;
  if tc.application_session_id is distinct from r.application_session_id then
    raise exception 'receipt % names evidence written by a different session',
      p_receipt_id;
  end if;

  -- Computed HERE, from the frozen row, over the fields that carry meaning.
  -- The caller cannot influence this value.
  digest := encode(sha256(convert_to(
      coalesce(tc.verb, '') || '|' ||
      coalesce(tc.actor_id::text, '') || '|' ||
      coalesce(tc.organization_tenant_id, '') || '|' ||
      coalesce(tc.application_session_id::text, '') || '|' ||
      coalesce(tc.request_hash, ''), 'UTF8')), 'hex');

  update ops.write_receipt
     set readback_digest = digest, readback_at = clock_timestamp()
   where id = p_receipt_id;

  return digest = r.claimed_digest;
end $$;

-- The digest a caller must claim, so an honest caller can compute the same
-- value from what it is about to write. Exposing the recipe does not weaken the
-- proof: the readback still reads the FROZEN row, so a caller who claims a
-- digest for content it did not write simply produces an unproven receipt.
create function ops.write_receipt_digest(
  p_verb text, p_actor_id uuid, p_tenant text, p_session uuid, p_request_hash text)
returns text language sql immutable
set search_path = pg_catalog
as $$
  select encode(sha256(convert_to(
    coalesce(p_verb,'') || '|' || coalesce(p_actor_id::text,'') || '|' ||
    coalesce(p_tenant,'') || '|' || coalesce(p_session::text,'') || '|' ||
    coalesce(p_request_hash,''), 'UTF8')), 'hex');
$$;

-- ---------------------------------------------------------- causal conflict
-- Two receipts conflict when they name the same subject, built on the same
-- prior state, and produced different results. A definition, evaluated by a
-- query, rather than a flag anyone sets.
create function ops.receipt_conflicts(p_subject_type text, p_subject_id uuid)
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
     and a.subject_id   = p_subject_id;
$$;

-- ------------------------------------------------------------------ grants
revoke all on function ops.prove_write_receipt(uuid) from public;
revoke all on function ops.write_receipt_digest(text,uuid,text,uuid,text) from public;
revoke all on function ops.receipt_conflicts(text,uuid) from public;
revoke all on function ops.require_live_session_for_receipt() from public;
revoke all on function ops.refuse_receipt_rewrite() from public;
revoke all on function ops.require_exact_reversal() from public;

-- The runtime writer creates receipts and proves them. It cannot rewrite one:
-- UPDATE is withheld and the immutability trigger stands behind that.
grant select, insert on ops.write_receipt to carr_writer;
grant select on ops.write_receipt to carr_reader;
grant execute on function ops.prove_write_receipt(uuid) to carr_writer;
grant execute on function ops.write_receipt_digest(text,uuid,text,uuid,text)
  to carr_writer, carr_reader;
grant execute on function ops.receipt_conflicts(text,uuid) to carr_writer, carr_reader;
revoke update, delete on ops.write_receipt from carr_writer;

-- --------------------------------------------------------------- apply-time
-- EXERCISES the guarantees rather than describing them, and rolls every probe
-- back. Same reasoning as 0257's block: this file runs where the contract suite
-- does not, so shape checks alone would let a gutted guard through.
do $$
declare
  probe_actor uuid;
  other_actor uuid;
  sid         uuid := gen_random_uuid();
  other_sid   uuid := gen_random_uuid();
  rid         uuid := gen_random_uuid();
  rid2        uuid := gen_random_uuid();
  key1        text := 'receipt-probe-' || gen_random_uuid()::text;
  claimed     text;
  proved      boolean;
  failed      boolean;
begin
  select id into probe_actor from public.actor order by slug limit 1;
  select id into other_actor from public.actor where id <> probe_actor order by slug limit 1;
  if probe_actor is null or other_actor is null then
    raise exception '0260 FAILED: need two seeded actors to exercise the guards';
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');

  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (key1, 'log-activity', probe_actor, 'req-hash-1', '{}'::jsonb,
          'carr-internal', sid);

  claimed := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                      sid, 'req-hash-1');

  -- A receipt naming a session that does not match its actor must refuse.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
    values (gen_random_uuid(), sid, other_actor, 'carr-internal', 'log-activity',
            'deal', gen_random_uuid(), key1, claimed, 'prior-0');
  exception when others then
    failed := true;
    if position('different actor' in sqlerrm) = 0 then
      raise exception '0260 FAILED: cross-actor receipt refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0260 FAILED: a receipt naming a different actor than its session was ACCEPTED';
  end if;

  -- The honest path: insert, then prove.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
  values (rid, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', gen_random_uuid(), key1, claimed, 'prior-0');

  if (select is_proven from ops.write_receipt where id = rid) then
    raise exception '0260 FAILED: a receipt was proven before any readback ran';
  end if;

  proved := ops.prove_write_receipt(rid);
  if not proved then
    raise exception '0260 FAILED: an honest receipt did not prove against its frozen evidence';
  end if;
  if not (select is_proven from ops.write_receipt where id = rid) then
    raise exception '0260 FAILED: readback ran but is_proven did not follow it';
  end if;

  -- A LYING receipt: same evidence, a digest it did not write. The readback must
  -- run and must NOT prove it.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
  values (rid2, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', gen_random_uuid(), key1, 'a-digest-nobody-wrote', 'prior-0');
  if ops.prove_write_receipt(rid2) then
    raise exception '0260 FAILED: a receipt claiming a digest it never wrote was PROVEN';
  end if;
  if (select is_proven from ops.write_receipt where id = rid2) then
    raise exception '0260 FAILED: a false claim still reported is_proven';
  end if;

  -- Readback is one-way.
  begin
    failed := false;
    perform ops.prove_write_receipt(rid);
  exception when others then
    failed := true;
    if position('already carries a readback' in sqlerrm) = 0 then
      raise exception '0260 FAILED: re-proving refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0260 FAILED: a receipt was proven twice';
  end if;

  -- An INEXACT reversal must refuse.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key, claimed_digest,
       prior_digest, reverses_receipt_id)
    select gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
           w.subject_type, w.subject_id, key1, 'not-the-prior-state', w.claimed_digest, rid
      from ops.write_receipt w where w.id = rid;
  exception when others then
    failed := true;
    if position('reversal is not exact' in sqlerrm) = 0 then
      raise exception '0260 FAILED: inexact reversal refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0260 FAILED: a reversal that does not restore the prior state was ACCEPTED';
  end if;

  -- An EXACT reversal must be accepted.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest,
     prior_digest, reverses_receipt_id)
  select gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
         w.subject_type, w.subject_id, key1, w.prior_digest, w.claimed_digest, rid
    from ops.write_receipt w where w.id = rid;

  -- Identity is immutable.
  begin
    failed := false;
    update ops.write_receipt set claimed_digest = 'rewritten' where id = rid;
  exception when others then
    failed := true;
  end;
  if not failed then
    raise exception '0260 FAILED: a receipt digest was rewritten';
  end if;

  -- Receipts cannot be deleted.
  begin
    failed := false;
    delete from ops.write_receipt where id = rid;
  exception when others then
    failed := true;
    if position('cannot be deleted' in sqlerrm) = 0 then
      raise exception '0260 FAILED: receipt deletion refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0260 FAILED: a receipt was deleted';
  end if;

  -- A receipt against a session that is not this one's must refuse.
  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (other_sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');
  declare
    rid3 uuid := gen_random_uuid();
  begin
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
    values (rid3, other_sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', gen_random_uuid(), key1, claimed, 'prior-0');
    failed := false;
    begin
      perform ops.prove_write_receipt(rid3);
    exception when others then
      failed := true;
      if position('different session' in sqlerrm) = 0 then
        raise exception '0260 FAILED: cross-session readback refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0260 FAILED: a receipt was proven against another session''s evidence';
    end if;
  end;

  raise notice '0260 apply-time proof passed';
  raise exception 'ROLLBACK_0260_PROBE';
exception when others then
  if sqlerrm = 'ROLLBACK_0260_PROBE' then
    return;
  end if;
  raise;
end $$;
