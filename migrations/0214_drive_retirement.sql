-- 0214 — Drive retirement, resolved from receipts rather than from a report
--
-- WHAT THE EXISTING PREFLIGHT ALREADY SAYS, IN ITS OWN WORDS. Asked whether a
-- static inventory can close Phase 4, ops/drive-retirement-readiness-gate.py
-- answers: "static registry cannot resolve immutable repoint receipts, recovery
-- receipts, or Joe's authority receipt. Use the record-layer verifier; caller
-- JSON is refused." That file deliberately has no --evidence argument, because
-- JSON supplied by a caller is not an immutable receipt.
--
-- This migration is the record layer that answer points at. It exists only
-- because 0211 gave us receipts that prove themselves by readback and 0213 gave
-- us an acceptance the runtime cannot make. Without those two, every table here
-- would be a place to write "yes, it is fine".
--
-- THE THREE FACTS THE PREFLIGHT SAYS IT CANNOT RESOLVE, AND HOW EACH IS:
--
--   A READER WAS REPOINTED — cited as a PROVEN receipt. Not a boolean, not a
--   filename, not a caller's assertion: a receipt whose digest the database
--   recomputed from frozen evidence.
--
--   RECOVERY WAS EXERCISED — a second proven receipt, and it must be a
--   DIFFERENT one. Repointing a reader and proving you can still recover from
--   it are two claims, and one receipt cannot make both.
--
--   JOE APPROVED THE BATCH — an ops.phase4_acceptance row, which only the
--   authority identity can create and which no machine actor can create at all.
--
-- NOTHING HERE IS A COMPLETION FLAG. Readiness is a function over the rows,
-- computed when asked, so it cannot drift from what was actually retired.

-- ------------------------------------------------ the inventory, as records
-- The static sweep in ops/drive-dependency-inventory.py finds the references.
-- This table is where an OPERATIONAL one becomes a thing that must be retired
-- with evidence rather than a line in a report.
create table ops.drive_dependency (
  id             uuid primary key default gen_random_uuid(),
  source_path    text not null,
  reference      text not null,
  classification text not null,
  operational    boolean not null,
  recorded_at    timestamptz not null default clock_timestamp(),
  constraint drive_dependency_unique unique (source_path, reference)
);

comment on table ops.drive_dependency is
  'One row per Drive reference the static inventory classified. operational=true '
  'means it must be retired with receipts before Drive retirement can close.';

-- ------------------------------------------------------------- retirement
create table ops.drive_retirement (
  id                     uuid primary key,
  drive_dependency_id    uuid not null references ops.drive_dependency(id),
  -- BOTH ARE RECEIPTS, and both must be proven. See the trigger.
  repoint_receipt_id     uuid not null references ops.write_receipt(id),
  recovery_receipt_id    uuid not null references ops.write_receipt(id),
  application_session_id uuid not null references ops.application_session(id),
  retired_by_actor_id    uuid not null references public.actor(id),
  organization_tenant_id text not null,
  retired_at             timestamptz not null default clock_timestamp(),
  note                   text not null,
  constraint drive_retirement_one_per_dependency unique (drive_dependency_id),
  -- ONE RECEIPT CANNOT MAKE TWO CLAIMS. Repointing a reader and proving
  -- recovery still works are different assertions, and letting a single receipt
  -- stand for both is how "we checked" becomes "we checked once, sort of".
  constraint drive_retirement_distinct_receipts
    check (repoint_receipt_id <> recovery_receipt_id),
  constraint drive_retirement_needs_a_note check (length(btrim(note)) > 0)
);

create function ops.require_proven_retirement_receipts()
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
  -- PROVEN, not merely present. An unproven receipt is a claim the database
  -- has already declined to confirm, and retirement on the strength of one
  -- would be retirement on the strength of nothing.
  if not repoint.is_proven then
    raise exception 'the repoint receipt % is not proven', new.repoint_receipt_id;
  end if;
  if not recovery.is_proven then
    raise exception 'the recovery receipt % is not proven', new.recovery_receipt_id;
  end if;
  return new;
end $$;

create trigger drive_retirement_requires_proven_receipts
before insert on ops.drive_retirement
for each row execute function ops.require_proven_retirement_receipts();
alter table ops.drive_retirement
  enable always trigger drive_retirement_requires_proven_receipts;

create function ops.refuse_retirement_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'a drive retirement record cannot be deleted';
  end if;
  raise exception 'a drive retirement record cannot be rewritten';
end $$;

create trigger drive_retirement_immutable
before update or delete on ops.drive_retirement
for each row execute function ops.refuse_retirement_rewrite();
alter table ops.drive_retirement enable always trigger drive_retirement_immutable;

-- ------------------------------------------------------------- readiness
-- A FUNCTION, NOT A FLAG. Computed from the rows every time it is asked, so it
-- cannot say "ready" about a state that has since changed.
create function ops.drive_retirement_readiness()
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
       ret as (select count(*) n from ops.drive_retirement r
                 join ops.drive_dependency d on d.id = r.drive_dependency_id
                where d.operational),
       auth as (select count(*) n from ops.phase4_acceptance)
  select op.n, ret.n, op.n - ret.n, auth.n > 0,
         op.n > 0 and op.n = ret.n and auth.n > 0
    from op, ret, auth;
$$;

comment on function ops.drive_retirement_readiness() is
  'ready requires: at least one operational dependency on record, every one of '
  'them retired with two proven receipts, and a phase acceptance that only the '
  'authority identity can create. An empty inventory is NOT ready — nothing '
  'proven about nothing is not proof.';

-- ------------------------------------------------------------------ grants
revoke all on function ops.require_proven_retirement_receipts() from public;
revoke all on function ops.refuse_retirement_rewrite() from public;
revoke all on function ops.drive_retirement_readiness() from public;

grant select, insert on ops.drive_dependency to carr_writer;
grant select on ops.drive_dependency to carr_reader;
grant select, insert on ops.drive_retirement to carr_writer;
grant select on ops.drive_retirement to carr_reader;
grant execute on function ops.drive_retirement_readiness() to carr_writer, carr_reader;
-- Retiring a dependency is operational work the runtime may do, ONCE it holds
-- two proven receipts. Declaring the phase closed is not, and lives in 0213's
-- acceptance, which carr_writer cannot execute.
revoke update, delete on ops.drive_retirement from carr_writer;
revoke update, delete on ops.drive_dependency from carr_writer;

-- --------------------------------------------------------------- apply-time
do $$
declare
  probe_actor uuid;
  sid         uuid := gen_random_uuid();
  key1        text := 'retire-probe-' || gen_random_uuid()::text;
  claimed     text;
  dep         uuid;
  r1          uuid := gen_random_uuid();
  r2          uuid := gen_random_uuid();
  subj        uuid := gen_random_uuid();
  rdy         record;
  failed      boolean;
begin
  select id into probe_actor from public.actor where kind = 'human' order by slug limit 1;
  if probe_actor is null then
    raise exception '0214 FAILED: need a human actor for the retirement probe';
  end if;

  -- An empty inventory must NOT read as ready.
  select * into rdy from ops.drive_retirement_readiness();
  if rdy.ready then
    raise exception '0214 FAILED: an empty inventory reported READY; nothing proven '
                    'about nothing is not proof';
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (key1, 'log-activity', probe_actor, 'rh-r', '{}'::jsonb, 'carr-internal', sid);
  claimed := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid, 'rh-r');

  -- THE DANGEROUS EMPTY CASE, and the one a naive probe misses. With no
  -- acceptance an empty inventory fails for the WRONG reason, so the state
  -- worth testing is: Phase 4 accepted, and not one Drive dependency ever
  -- recorded. Without the "at least one operational dependency" clause that
  -- reads as fully retired — a system that never looked reporting that it
  -- finished. Found by mutation testing.
  --
  -- Run inside a subtransaction and rolled back, so the acceptance it needs
  -- does not leak into the checks below, which require that none exists yet.
  begin
    insert into ops.phase4_acceptance
      (id, application_session_id, accepted_by_actor_id, organization_tenant_id,
       qualifying_tool_calls, qualifying_events, qualifying_read_calls,
       proven_receipts, unproven_receipts, open_conflicts, note)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal',
            1, 1, 1, 1, 0, 0, '0214 empty-case probe');
    select * into rdy from ops.drive_retirement_readiness();
    if rdy.ready then
      raise exception '0214 FAILED: an empty inventory with an acceptance on record '
                      'reported READY';
    end if;
    raise exception 'ROLLBACK_EMPTY_CASE';
  exception when others then
    if sqlerrm <> 'ROLLBACK_EMPTY_CASE' then raise; end if;
  end;

  insert into ops.drive_dependency (source_path, reference, classification, operational)
  values ('tools/probe.py:1', '{{VAULT}}', 'vault-path', true)
  returning id into dep;

  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
  values (r1, sid, probe_actor, 'carr-internal', 'log-activity',
          'drive_dependency', subj, key1, claimed, 'origin');
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
  values (r2, sid, probe_actor, 'carr-internal', 'log-activity',
          'drive_dependency', gen_random_uuid(), key1, claimed, 'origin');

  -- UNPROVEN RECEIPTS MUST NOT RETIRE ANYTHING.
  begin
    failed := false;
    insert into ops.drive_retirement
      (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
       application_session_id, retired_by_actor_id, organization_tenant_id, note)
    values (gen_random_uuid(), dep, r1, r2, sid, probe_actor, 'carr-internal', 'probe');
  exception when others then
    failed := true;
    if position('is not proven' in sqlerrm) = 0 then
      raise exception '0214 FAILED: unproven-receipt retirement refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0214 FAILED: a dependency was retired on UNPROVEN receipts';
  end if;

  perform ops.prove_write_receipt(r1);
  perform ops.prove_write_receipt(r2);

  -- ONE RECEIPT CANNOT MAKE BOTH CLAIMS.
  begin
    failed := false;
    insert into ops.drive_retirement
      (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
       application_session_id, retired_by_actor_id, organization_tenant_id, note)
    values (gen_random_uuid(), dep, r1, r1, sid, probe_actor, 'carr-internal', 'probe');
  exception when others then
    failed := true;
    if position('drive_retirement_distinct_receipts' in sqlerrm) = 0 then
      raise exception '0214 FAILED: same-receipt retirement refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0214 FAILED: one receipt was accepted for BOTH the repoint and the recovery';
  end if;

  -- The honest path.
  insert into ops.drive_retirement
    (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
     application_session_id, retired_by_actor_id, organization_tenant_id, note)
  values (gen_random_uuid(), dep, r1, r2, sid, probe_actor, 'carr-internal',
          'probe retirement');

  -- Every dependency retired, but NO authority acceptance yet.
  select * into rdy from ops.drive_retirement_readiness();
  if rdy.remaining <> 0 then
    raise exception '0214 FAILED: a retired dependency still counted as remaining';
  end if;
  if rdy.ready then
    raise exception '0214 FAILED: retirement read as READY without an authority acceptance';
  end if;

  -- AND THE HAPPY PATH MUST BE REACHABLE. A gate that can only ever say no is
  -- indistinguishable from a gate that is broken, and nothing above would have
  -- caught one.
  insert into ops.phase4_acceptance
    (id, application_session_id, accepted_by_actor_id, organization_tenant_id,
     qualifying_tool_calls, qualifying_events, qualifying_read_calls,
     proven_receipts, unproven_receipts, open_conflicts, note)
  values (gen_random_uuid(), sid, probe_actor, 'carr-internal',
          1, 1, 1, 1, 0, 0, '0214 probe acceptance');
  select * into rdy from ops.drive_retirement_readiness();
  if not rdy.ready then
    raise exception '0214 FAILED: every dependency retired on proven receipts and an '
                    'acceptance on record, and readiness still said no';
  end if;

  -- Retirement records are immutable.
  begin
    failed := false;
    update ops.drive_retirement set note = 'rewritten' where drive_dependency_id = dep;
  exception when others then
    failed := true;
    if position('cannot be rewritten' in sqlerrm) = 0 then
      raise exception '0214 FAILED: retirement rewrite refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0214 FAILED: a retirement record was rewritten';
  end if;

  raise notice '0214 apply-time proof passed';
  raise exception 'ROLLBACK_0214_PROBE';
exception when others then
  if sqlerrm = 'ROLLBACK_0214_PROBE' then
    return;
  end if;
  raise;
end $$;
