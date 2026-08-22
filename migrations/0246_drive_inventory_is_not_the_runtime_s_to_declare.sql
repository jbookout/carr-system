-- 0246 — the Drive retirement denominator stops being the runtime's to choose
--
-- THE HOLE, AS FOUND. ops.drive_retirement_readiness() answers "is Drive
-- retirement done?" with `op.n > 0 and op.n = ret.n and auth.n > 0`, where op.n
-- is `select count(*) from ops.drive_dependency where operational`. 0243 granted
-- carr_writer INSERT on that table, and NOTHING IN THE REPOSITORY EVER
-- POPULATES IT. So the denominator of the completion test was whatever the
-- runtime credential happened to have written, and the cheapest possible route
-- to a READY verdict was:
--
--   insert ONE operational dependency you invented,
--   retire it with two proven receipts (which the runtime can legitimately
--     produce, because it holds carr_writer),
--   and the gate reports every operational Drive dependency retired.
--
-- Every downstream guard was real. ops.require_proven_retirement_receipts
-- genuinely checks proof; the receipts genuinely prove by readback. They were
-- all guarding a number the guarded party supplied. This is the same shape as
-- the defect 0243 itself was written against — a static report saying "yes, it
-- is fine" — moved one table to the left.
--
-- WHY A DIGEST AND NOT JUST A PRIVILEGE. Taking INSERT away from carr_writer is
-- necessary and is done below, but on its own it only moves the question: now
-- the AUTHORITY identity supplies the denominator, and an authority that loads
-- three of thirty dependencies gets the same false READY. So readiness also
-- requires that the rows in this database are the rows the static inventory
-- actually found, compared by a digest the DATABASE computes over its own rows
-- and the INVENTORY TOOL computes over the repository. Neither side can be
-- talked into agreeing with the other; they either hash the same or they do not.
--
-- WHAT THIS STILL DOES NOT DO, stated plainly. The manifest digest is a claim
-- made by whoever runs the inventory tool, and carr_authority can declare any
-- digest it likes for rows it has already written. What it cannot do is declare
-- a digest and then quietly change the rows, or load a partial inventory that
-- matches a digest computed over the full one. The fabrication has to be
-- CONSISTENT and it has to happen at the authority identity rather than at the
-- credential every verb already holds — which is exactly the reduction 0242
-- made for acceptance, applied to the denominator acceptance is measured over.

-- ================================ (A) what this database says its inventory is

-- ORDERED BY THE WHOLE LINE, UNDER THE C COLLATION, DELIBERATELY. `order by` in
-- Postgres uses the database collation, so the same rows hash differently on a
-- cluster initdb'd under en_US.UTF-8 than on one under C -- and the disposable
-- harness runs under C while a hosted database very likely does not. A digest
-- whose value depends on the host's locale is not a digest. `collate "C"` sorts
-- by byte value, which for UTF-8 is code-point order, which is exactly what
-- Python's sorted() over str produces on the other side of this comparison.
create function ops.drive_dependency_digest()
returns text
language sql stable
set search_path = pg_catalog, ops, public
as $$
  select encode(sha256(convert_to(
           coalesce(string_agg(line, E'\n' order by line collate "C"), ''), 'UTF8')), 'hex')
    from (
      select d.source_path || '|' || d.reference || '|' || d.classification || '|' ||
             (case when d.operational then 't' else 'f' end) as line
        from ops.drive_dependency d
    ) s;
$$;

comment on function ops.drive_dependency_digest() is
  'sha256 over this database''s drive_dependency rows, one canonical line per '
  'row as source_path|reference|classification|t-or-f, sorted under the C '
  'collation and joined with newlines. ops/drive-dependency-inventory.py '
  '--emit-manifest computes the same digest from the repository. An empty '
  'inventory hashes the empty string, which no repository inventory can equal.';

-- ============================= (B) what the static inventory says the repo has

create table ops.drive_inventory_manifest (
  id                     uuid primary key,
  seq                    bigint generated always as identity,
  inventory_digest       text not null,
  application_session_id uuid not null references ops.application_session(id),
  declared_by_actor_id   uuid not null references public.actor(id),
  organization_tenant_id text not null,
  declared_at            timestamptz not null default clock_timestamp(),
  note                   text not null,
  -- A sha256 in hex is 64 characters and nothing else is. Checking the SHAPE
  -- here means a truncated or empty digest is refused at the door rather than
  -- silently failing to match forever, which reads identically from outside.
  constraint drive_inventory_manifest_digest_is_sha256
    check (inventory_digest ~ '^[0-9a-f]{64}$'),
  constraint drive_inventory_manifest_needs_a_note check (length(btrim(note)) > 0)
);

comment on table ops.drive_inventory_manifest is
  'One row per declaration of what the static Drive inventory found in the '
  'repository. The CURRENT manifest is the highest seq. Readiness compares its '
  'digest to ops.drive_dependency_digest() over this database''s own rows, so a '
  'declaration and the rows it describes cannot drift apart unnoticed.';

-- SEQ, NOT declared_at. The same reasoning 0244 applied to the receipt fold:
-- declared_at is clock_timestamp() and two declarations inside one tick would
-- tie, leaving "which manifest is current" to be settled by a primary key that
-- is a random uuid. An identity column is monotonic and no caller can write it.
create function ops.current_drive_inventory_manifest()
returns ops.drive_inventory_manifest
language sql stable
set search_path = pg_catalog, ops, public
as $$
  select * from ops.drive_inventory_manifest order by seq desc limit 1;
$$;

create function ops.require_live_session_for_manifest()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s ops.application_session%rowtype;
begin
  select * into s from ops.application_session
   where id = new.application_session_id for share;
  if not found then
    raise exception 'unknown application session % for inventory manifest',
      new.application_session_id;
  end if;
  if s.revoked_at is not null then
    raise exception 'application session % is revoked', new.application_session_id;
  end if;
  if clock_timestamp() >= s.expires_at then
    raise exception 'application session % is expired', new.application_session_id;
  end if;
  if new.declared_by_actor_id is distinct from s.actor_id then
    raise exception 'inventory manifest names a different actor than its session';
  end if;
  if new.organization_tenant_id is distinct from s.organization_tenant_id then
    raise exception 'inventory manifest names a different tenant than its session';
  end if;
  return new;
end $$;

create trigger drive_inventory_manifest_requires_live_session
before insert on ops.drive_inventory_manifest
for each row execute function ops.require_live_session_for_manifest();
alter table ops.drive_inventory_manifest
  enable always trigger drive_inventory_manifest_requires_live_session;

create function ops.refuse_manifest_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'an inventory manifest cannot be deleted';
  end if;
  raise exception 'an inventory manifest cannot be rewritten';
end $$;

-- SUPERSEDED, NEVER EDITED. A wrong manifest is corrected by declaring another
-- one, which is the same posture 0244 gave a wrong retirement: the mistake and
-- its correction both stay on the record, and "what did we believe on Tuesday"
-- remains answerable.
create trigger drive_inventory_manifest_immutable
before update or delete on ops.drive_inventory_manifest
for each row execute function ops.refuse_manifest_rewrite();
alter table ops.drive_inventory_manifest
  enable always trigger drive_inventory_manifest_immutable;

-- ==================================== (C) readiness reads the binding as well

drop function ops.drive_retirement_readiness();

create function ops.drive_retirement_readiness()
returns table (
  operational_total bigint,
  retired_total     bigint,
  remaining         bigint,
  has_authority     boolean,
  declared_digest   text,
  observed_digest   text,
  inventory_bound   boolean,
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
       auth as (select count(*) n from ops.phase4_acceptance),
       man as (select (ops.current_drive_inventory_manifest()).inventory_digest d),
       obs as (select ops.drive_dependency_digest() d)
  select op.n, ret.n, op.n - ret.n, auth.n > 0,
         man.d, obs.d,
         man.d is not null and man.d = obs.d,
         op.n > 0
           and op.n = ret.n
           and auth.n > 0
           and man.d is not null
           and man.d = obs.d
    from op, ret, auth, man, obs;
$$;

comment on function ops.drive_retirement_readiness() is
  'ready requires: an inventory manifest whose digest matches this database''s '
  'own drive_dependency rows, at least one operational dependency on record, '
  'every one of them retired with two proven receipts and NOT withdrawn, and a '
  'phase acceptance only the authority identity can create. Counts DISTINCT '
  'dependencies. An empty inventory is NOT ready, and an inventory nobody '
  'declared is NOT ready however thoroughly it was retired.';

-- ------------------------------------------------------------------ grants
--
-- THE INVENTORY IS NOT THE RUNTIME'S TO WRITE. This is the whole point of the
-- migration. carr_writer keeps everything it needs to do the WORK of retiring a
-- dependency -- it still inserts ops.drive_retirement and ops.write_receipt --
-- and loses the ability to decide how many dependencies there were, which is
-- the same division 0242 drew when it put accept_phase4 on carr_authority: do
-- the work with the ordinary credential, declare what the work amounts to with
-- the authority one.
revoke insert on ops.drive_dependency from carr_writer;
grant select, insert, delete on ops.drive_dependency to carr_authority;
grant select, insert on ops.drive_inventory_manifest to carr_authority;
grant select on ops.drive_inventory_manifest to carr_reader, carr_writer;
revoke update, delete on ops.drive_inventory_manifest from carr_writer, carr_authority;
revoke all on function ops.require_live_session_for_manifest() from public;
revoke all on function ops.refuse_manifest_rewrite() from public;
revoke all on function ops.drive_dependency_digest() from public;
revoke all on function ops.current_drive_inventory_manifest() from public;
revoke all on function ops.drive_retirement_readiness() from public;
grant execute on function ops.drive_dependency_digest()
  to carr_writer, carr_reader, carr_authority;
grant execute on function ops.current_drive_inventory_manifest()
  to carr_writer, carr_reader, carr_authority;
grant execute on function ops.drive_retirement_readiness()
  to carr_writer, carr_reader, carr_authority;

-- --------------------------------------------------------------- apply-time
--
-- WHAT THIS BLOCK CAN AND CANNOT PROVE, because 0242's rule now binds it: an
-- acceptance may not count evidence its own transaction wrote, so anything here
-- that needs ops.phase4_acceptance has to write evidence first and is therefore
-- refused. The clauses of the acceptance BAR are contract-suite work. What is
-- provable here is the part this migration actually adds -- the digest, the
-- manifest, and the binding clause in readiness -- and that is what it proves.
do $$
declare
  probe_actor uuid;
  sid         uuid := gen_random_uuid();
  dep         uuid;
  d_before    text;
  d_after     text;
  rdy         record;
  failed      boolean;
begin
  select id into probe_actor from public.actor where kind = 'human' order by slug limit 1;
  if probe_actor is null then
    raise exception '0246 FAILED: need a human actor for the manifest probe';
  end if;

  -- AN EMPTY INVENTORY HASHES THE EMPTY STRING, and that value must be stable:
  -- it is the one digest a repository inventory can never legitimately produce,
  -- because the repository has references in it.
  if ops.drive_dependency_digest()
     <> encode(sha256(convert_to('', 'UTF8')), 'hex') then
    raise exception '0246 FAILED: an empty inventory did not hash the empty string';
  end if;

  -- NO MANIFEST MEANS NOT BOUND, whatever else is true.
  select * into rdy from ops.drive_retirement_readiness();
  if rdy.inventory_bound then
    raise exception '0246 FAILED: readiness reported the inventory BOUND with no '
                    'manifest on record';
  end if;
  if rdy.ready then
    raise exception '0246 FAILED: readiness said yes with no manifest on record';
  end if;

  -- THE DIGEST MUST ACTUALLY DEPEND ON THE ROWS. A function that returned a
  -- constant would satisfy every check above and bind nothing at all.
  d_before := ops.drive_dependency_digest();
  insert into ops.drive_dependency (source_path, reference, classification, operational)
  values ('tools/probe.py:1', '{{VAULT}}', 'vault-path', true)
  returning id into dep;
  d_after := ops.drive_dependency_digest();
  if d_after = d_before then
    raise exception '0246 FAILED: adding a dependency did not change the inventory digest';
  end if;

  -- AND IT MUST DEPEND ON THE operational FLAG, not merely on row identity.
  -- The flag is the one column that moves a row into and out of the
  -- denominator, so a digest blind to it would let the denominator be changed
  -- while the binding still reported a match.
  update ops.drive_dependency set operational = false where id = dep;
  if ops.drive_dependency_digest() = d_after then
    raise exception '0246 FAILED: flipping operational did not change the digest';
  end if;
  update ops.drive_dependency set operational = true where id = dep;
  if ops.drive_dependency_digest() <> d_after then
    raise exception '0246 FAILED: the digest is not a function of the rows alone';
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');

  -- A DIGEST THAT IS NOT A SHA256 IS REFUSED AT THE DOOR.
  begin
    failed := false;
    insert into ops.drive_inventory_manifest
      (id, inventory_digest, application_session_id, declared_by_actor_id,
       organization_tenant_id, note)
    values (gen_random_uuid(), 'not-a-digest', sid, probe_actor, 'carr-internal', 'probe');
  exception when others then
    failed := true;
    if position('drive_inventory_manifest_digest_is_sha256' in sqlerrm) = 0 then
      raise exception '0246 FAILED: a malformed digest was refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0246 FAILED: a manifest carrying a non-sha256 digest was accepted';
  end if;

  -- A MANIFEST THAT DESCRIBES DIFFERENT ROWS DOES NOT BIND. This is the clause
  -- that closes the hole: declaring a digest is not the same as declaring one
  -- that matches what is actually here.
  insert into ops.drive_inventory_manifest
    (id, inventory_digest, application_session_id, declared_by_actor_id,
     organization_tenant_id, note)
  values (gen_random_uuid(), encode(sha256(convert_to('some other inventory', 'UTF8')), 'hex'),
          sid, probe_actor, 'carr-internal', '0246 probe: a manifest for other rows');
  select * into rdy from ops.drive_retirement_readiness();
  if rdy.inventory_bound then
    raise exception '0246 FAILED: a manifest describing OTHER rows reported BOUND';
  end if;
  if rdy.ready then
    raise exception '0246 FAILED: readiness said yes on a manifest that does not '
                    'describe this database''s inventory';
  end if;

  -- AND A MATCHING ONE DOES. A binding that can only ever say no is
  -- indistinguishable from a broken one.
  insert into ops.drive_inventory_manifest
    (id, inventory_digest, application_session_id, declared_by_actor_id,
     organization_tenant_id, note)
  values (gen_random_uuid(), ops.drive_dependency_digest(), sid, probe_actor,
          'carr-internal', '0246 probe: the manifest for these rows');
  select * into rdy from ops.drive_retirement_readiness();
  if not rdy.inventory_bound then
    raise exception '0246 FAILED: a manifest matching these exact rows did not bind';
  end if;
  if rdy.declared_digest is distinct from rdy.observed_digest then
    raise exception '0246 FAILED: readiness reported a bound inventory whose two '
                    'digests differ';
  end if;

  -- THE HIGHEST SEQ WINS, so a correction supersedes rather than edits.
  if (ops.current_drive_inventory_manifest()).note
     <> '0246 probe: the manifest for these rows' then
    raise exception '0246 FAILED: the current manifest is not the most recent one';
  end if;

  -- AND CHANGING THE ROWS AFTER DECLARING BREAKS THE BINDING AGAIN.
  insert into ops.drive_dependency (source_path, reference, classification, operational)
  values ('tools/probe.py:2', '{{VAULT}}', 'vault-path', true);
  select * into rdy from ops.drive_retirement_readiness();
  if rdy.inventory_bound then
    raise exception '0246 FAILED: rows changed after the manifest was declared and '
                    'the binding still reported a match';
  end if;

  -- MANIFESTS ARE IMMUTABLE.
  begin
    failed := false;
    update ops.drive_inventory_manifest set note = 'rewritten';
  exception when others then
    failed := true;
    if position('cannot be rewritten' in sqlerrm) = 0 then
      raise exception '0246 FAILED: manifest rewrite refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0246 FAILED: an inventory manifest was rewritten';
  end if;

  raise notice '0246 apply-time proof passed';
  raise exception 'ROLLBACK_0246_PROBE';
exception when others then
  if sqlerrm = 'ROLLBACK_0246_PROBE' then
    return;
  end if;
  raise;
end $$;
