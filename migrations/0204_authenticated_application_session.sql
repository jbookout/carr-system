-- 0204_authenticated_application_session.sql
--
-- NUMBERED 0198 WHILE IT WAS BEING WRITTEN, AND RENUMBERED BEFORE IT APPLIED
-- ANYWHERE. 0199 merged to main during the same session, and 0200-0203 were
-- claimed by concurrent worktrees; leaving this at 0198 would have made it a
-- pending EARLIER migration sitting beneath already-applied later ones, which
-- tools/migrate.py refuses outright — and that refusal blocks every subsequent
-- migration on that database, not just this one. The rename window closes the
-- moment a file applies anywhere, so the self-references below were corrected
-- in the same change rather than left pointing at a number that no longer
-- exists. Two migrations in this series already carry uncorrectable labels
-- from a rename that came too late; see migrations/README.md.
--
-- The authenticated application-session substrate, and NOTHING else. This
-- migration creates no Phase 4 receipt, no continuity reducer, no acceptance
-- state, and no Drive-retirement claim. Those are later slices and must not
-- borrow this one's name.
--
-- WHAT THE TWO REJECTED ATTEMPTS GOT WRONG, and what is done differently here.
--
-- 1. The previous foundation granted EXECUTE on its minting function to
--    carr_writer — the very credential the Worker carries in
--    DATABASE_URL_WRITER — and let the caller supply authenticated_at. Any
--    holder of the routine write credential could therefore mint a permanent
--    row asserting that a partner authenticated through a browser door on a
--    date of the caller's choosing. Here the mint function is granted ONLY to
--    carr_session_minter, a role the runtime writer is not a member of, and
--    there is NO authenticated_at parameter at all: the value is taken from
--    the server clock, so backdating is not defended against, it is
--    unexpressible.
--
-- 2. The previous foundation added the link columns as bare references with no
--    INSERT-side guard, which made "atomic binding" a JavaScript convention
--    rather than a database invariant: a direct INSERT could hang any live
--    session on any actor's row, including another partner's. Here a BEFORE
--    INSERT trigger requires that a non-null link name a session that is live
--    AND whose actor and tenant match the row being written.
--
-- 3. The previous foundation made the session row immutable against UPDATE and
--    DELETE, which is correct for identity but foreclosed revocation — there
--    was nowhere to write it, and no expiry column either. Here identity is
--    immutable and revocation is the single permitted mutation: revoked_at may
--    go from NULL to a timestamp exactly once, and nothing else may change.
--
-- 4. Every trigger below is ENABLE ALWAYS. A plain CREATE TRIGGER is
--    ENABLE ORIGIN, which any role able to set session_replication_role to
--    'replica' switches off wholesale — after which one UPDATE promotes
--    arbitrary legacy history into qualified evidence.
--
-- Legacy history is preserved by leaving the link nullable. A NULL link is the
-- explicit, permanent statement that a row predates this contract; the
-- no-promotion trigger prevents it from ever being backfilled into evidence.

begin;

-- ---------------------------------------------------------------- the record

create table ops.application_session (
  id                     uuid primary key,
  actor_id               uuid        not null references public.actor(id),
  organization_tenant_id text        not null,
  -- Nullable on purpose, in two directions. Bearer-token agents have no
  -- sponsoring human at all, and Dell must never be structurally required for
  -- a Joe-sponsored session to exist.
  sponsoring_human_slug  text,
  via                    text        not null,   -- the authenticated door
  auth_issuer            text        not null,   -- who verified the principal
  authorization_class    text        not null,
  verified_subject       text        not null,   -- the verified principal
  authenticated_at       timestamptz not null default clock_timestamp(),
  expires_at             timestamptz not null,
  revoked_at             timestamptz,
  revocation_reason      text,
  recorded_at            timestamptz not null default clock_timestamp(),
  constraint application_session_expires_after_auth
    check (expires_at > authenticated_at),
  constraint application_session_revocation_paired
    check ((revoked_at is null) = (revocation_reason is null)),
  constraint application_session_revoked_after_auth
    check (revoked_at is null or revoked_at >= authenticated_at)
);

comment on table ops.application_session is
  'Server-authenticated application-session identity, minted only at an authenticated '
  'door through ops.mint_application_session. Identity is immutable; revocation is the '
  'one permitted mutation. This is predecessor substrate, NOT a Phase 4 receipt, '
  'continuity acceptance, or evidence reducer.';
comment on column ops.application_session.authenticated_at is
  'Server clock at mint time. There is deliberately no parameter for this: a caller '
  'cannot propose it, so historical rows cannot be re-timed as fresh evidence.';

create index application_session_actor_live
  on ops.application_session (actor_id, expires_at) where revoked_at is null;

-- ------------------------------------------------- minting, and who may mint

-- The runtime writer is NOT a member of this role. Separating the mint
-- credential from the write credential is the whole point: a leaked or misused
-- DATABASE_URL_WRITER must not be able to manufacture an authenticated session.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_session_minter') then
    -- NOLOGIN and deliberately memberless. Nothing can mint until a later slice
    -- decides which door credential joins this role; until then the substrate is
    -- inert by construction, which is the intended state for a floor with no
    -- application wiring above it yet.
    create role carr_session_minter nologin;
  end if;
end $$;

create function ops.mint_application_session(
  p_id                     uuid,
  p_actor_id               uuid,
  p_organization_tenant_id text,
  p_sponsoring_human_slug  text,
  p_via                    text,
  p_auth_issuer            text,
  p_authorization_class    text,
  p_verified_subject       text,
  p_expires_at             timestamptz
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
begin
  if p_id is null or p_actor_id is null then
    raise exception 'application session requires an id and an actor';
  end if;
  if p_via is null or p_auth_issuer is null
     or p_authorization_class is null or p_verified_subject is null then
    raise exception 'application session requires door, issuer, class and verified subject';
  end if;
  if p_expires_at is null or p_expires_at <= clock_timestamp() then
    raise exception 'application session must expire in the future';
  end if;
  -- Bounded from ABOVE as well. "Expiry exists" is satisfied literally by a
  -- session that expires in the year 9999, which is a permanent credential
  -- wearing an expiry column.
  if p_expires_at > clock_timestamp() + interval '30 days' then
    raise exception 'application session lifetime may not exceed 30 days';
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via,
     auth_issuer, authorization_class, verified_subject, expires_at)
  values
    (p_id, p_actor_id, p_organization_tenant_id, p_sponsoring_human_slug, p_via,
     p_auth_issuer, p_authorization_class, p_verified_subject, p_expires_at);
  return p_id;
end $$;

create function ops.revoke_application_session(p_id uuid, p_reason text)
returns void
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
begin
  if p_reason is null or length(btrim(p_reason)) = 0 then
    raise exception 'revocation requires a reason';
  end if;
  update ops.application_session
     set revoked_at = clock_timestamp(), revocation_reason = p_reason
   where id = p_id and revoked_at is null;
  -- Silence here is how an incident responder who fat-fingers a session id is
  -- told the session is revoked while it is still live. Say which case it was.
  if not found then
    if exists (select 1 from ops.application_session where id = p_id) then
      raise exception 'application session % is already revoked', p_id;
    end if;
    raise exception 'no such application session %', p_id;
  end if;
end $$;

-- Is this session usable for qualified evidence RIGHT NOW?
create function ops.application_session_is_live(p_id uuid)
returns boolean
-- VOLATILE, not STABLE: it reads clock_timestamp(). The label was wrong rather
-- than harmful, but a planner is entitled to believe STABLE.
language sql volatile
set search_path = pg_catalog, ops, public
as $$
  select exists (
    select 1 from ops.application_session
     where id = p_id and revoked_at is null and clock_timestamp() < expires_at);
$$;

-- ------------------------------------------------ identity is immutable

create function ops.refuse_application_session_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'application session rows cannot be deleted';
  end if;
  -- The ONLY permitted change is an un-revoked row acquiring a revocation.
  if old.revoked_at is not null then
    raise exception 'application session revocation is already recorded and is final';
  end if;
  if (new.id, new.actor_id, new.organization_tenant_id, new.via, new.auth_issuer,
      new.authorization_class, new.verified_subject, new.authenticated_at,
      new.expires_at, new.recorded_at) is distinct from
     (old.id, old.actor_id, old.organization_tenant_id, old.via, old.auth_issuer,
      old.authorization_class, old.verified_subject, old.authenticated_at,
      old.expires_at, old.recorded_at)
     or new.sponsoring_human_slug is distinct from old.sponsoring_human_slug then
    raise exception 'application session identity is immutable';
  end if;
  if new.revoked_at is null then
    raise exception 'the only permitted update is setting revoked_at';
  end if;
  return new;
end $$;

create trigger application_session_identity_immutable
before update or delete on ops.application_session
for each row execute function ops.refuse_application_session_rewrite();
alter table ops.application_session
  enable always trigger application_session_identity_immutable;

-- ------------------------------------------------ the link on audit tables

alter table public.tool_call
  add column application_session_id uuid references ops.application_session(id);
alter table public.event
  add column application_session_id uuid references ops.application_session(id);
alter table public.tool_read_call
  add column application_session_id uuid references ops.application_session(id);

comment on column public.tool_call.application_session_id is
  'NULL means the row predates the authenticated-session contract and is permanently '
  'ineligible for Phase 4 evidence. It cannot be backfilled: see the no-promotion trigger.';

-- INSERT-side enforcement. This is what makes binding an invariant rather than
-- a convention in the JavaScript layer.
-- SECURITY DEFINER, and the reason is specific. The guard must take a share
-- lock (see FOR SHARE below), but PostgreSQL requires UPDATE privilege for any
-- row-locking SELECT, and UPDATE on this table is exactly what the runtime
-- writer must never hold. Granting it back would hand the writer the privilege
-- this migration exists to withhold. Running the guard as its definer takes the
-- lock without widening the caller. The body reads one row by primary key and
-- raises; it executes no dynamic SQL and nothing the caller can influence, and
-- search_path is pinned.
--
-- Getting this wrong is not a loud failure. With the writer lacking the
-- privilege, a qualified INSERT is refused while the legacy NULL-link path
-- returns early and still succeeds — so the fleet keeps running and quietly
-- produces nothing but non-qualifying rows.
create function ops.require_live_application_session()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s ops.application_session%rowtype;
begin
  if new.application_session_id is null then
    return new;                       -- the explicit legacy / non-qualifying path
  end if;
  -- LOCK ORDER, and the deadlock this can produce. FOR SHARE here is a NEW lock
  -- taken inside the envelope transaction, which already takes others. Two
  -- transactions that each bind evidence to one session while revoking another,
  -- cross-wise, can deadlock (40P01), and the writer is the one that loses. What
  -- keeps this off the hot path is that a single envelope binds exactly ONE
  -- session: the tool_call row and its event carry the same session id, so one
  -- transaction takes one share lock and there is no ordering to get wrong. A
  -- future caller that binds two different sessions in one transaction
  -- reintroduces the risk, and nothing here prevents that. There is no retry on
  -- 40P01 anywhere in the runtime today.
  --
  -- FOR SHARE, not a bare SELECT. Without it, revocation cannot fence off evidence
  -- already in flight: an open transaction that passed this check commits its row
  -- after a concurrent revoke has committed, and the window is the writer's own
  -- transaction duration. FOR SHARE conflicts with the FOR NO KEY UPDATE that
  -- ops.revoke_application_session takes, so the two serialise.
  select * into s from ops.application_session
   where id = new.application_session_id for share;
  if not found then
    raise exception 'unknown application session %', new.application_session_id;
  end if;
  if s.revoked_at is not null then
    raise exception 'application session % is revoked', new.application_session_id;
  end if;
  if clock_timestamp() >= s.expires_at then
    raise exception 'application session % is expired', new.application_session_id;
  end if;
  -- TENANT. This was named in this file's own header before it was implemented,
  -- which is exactly the kind of comment that makes a review believe a guarantee
  -- exists. A qualified row must carry the tenant its session was minted under;
  -- otherwise evidence for one tenant can be filed under another's session.
  if new.organization_tenant_id is distinct from s.organization_tenant_id then
    raise exception 'application session % belongs to a different tenant (% vs %)',
      new.application_session_id, new.organization_tenant_id, s.organization_tenant_id;
  end if;
  -- tool_read_call identifies its actor by slug; the other two carry actor_id.
  if tg_table_name = 'tool_read_call' then
    -- BOTH columns. This table carries actor_slug AND actor_id, and the verb
    -- layer populates both. Validating only the slug allowed a row naming one
    -- partner's slug and the other partner's actor id to bind to a live session
    -- — and because qualified rows are frozen, that contradiction could then be
    -- neither corrected nor removed.
    if new.actor_slug is distinct from (select slug from public.actor where id = s.actor_id) then
      raise exception 'application session % does not belong to actor %',
        new.application_session_id, new.actor_slug;
    end if;
    if new.actor_id is not null and new.actor_id is distinct from s.actor_id then
      raise exception 'application session % belongs to a different actor', 
        new.application_session_id;
    end if;
  else
    if new.actor_id is distinct from s.actor_id then
      raise exception 'application session % belongs to a different actor',
        new.application_session_id;
    end if;
  end if;
  return new;
end $$;

create trigger tool_call_requires_live_session
before insert on public.tool_call
for each row execute function ops.require_live_application_session();
alter table public.tool_call enable always trigger tool_call_requires_live_session;

create trigger event_requires_live_application_session
before insert on public.event
for each row execute function ops.require_live_application_session();
alter table public.event enable always trigger event_requires_live_application_session;

create trigger tool_read_call_requires_live_session
before insert on public.tool_read_call
for each row execute function ops.require_live_application_session();
alter table public.tool_read_call enable always trigger tool_read_call_requires_live_session;

-- No promotion by UPDATE, in either direction.
--
-- SCOPE, stated precisely because the earlier wording ("in either direction,
-- ever") was wrong: these triggers are UPDATE-scoped, and the freeze guard below
-- refuses DELETE only for rows that are already QUALIFIED. A LEGACY row — one
-- whose link is NULL — can still in principle be deleted and reinserted bound to
-- a live session, which would promote it. Nothing in this migration forbids that;
-- what stands in the way is that carr_writer holds no DELETE privilege on these
-- tables, which is an ACL fact rather than an invariant. The contract suite pins
-- that ACL so a future grant cannot silently reopen the path, and closing it
-- properly needs a retention design this slice does not have.
create function ops.refuse_application_session_relink()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception
    'application_session_id cannot be changed after the row is written '
    '(attempted % -> %)', old.application_session_id, new.application_session_id;
end $$;

create trigger tool_call_application_session_final
before update of application_session_id on public.tool_call
for each row when (old.application_session_id is distinct from new.application_session_id)
execute function ops.refuse_application_session_relink();
alter table public.tool_call enable always trigger tool_call_application_session_final;

create trigger event_application_session_final
before update of application_session_id on public.event
for each row when (old.application_session_id is distinct from new.application_session_id)
execute function ops.refuse_application_session_relink();
alter table public.event enable always trigger event_application_session_final;

create trigger tool_read_call_application_session_final
before update of application_session_id on public.tool_read_call
for each row when (old.application_session_id is distinct from new.application_session_id)
execute function ops.refuse_application_session_relink();
alter table public.tool_read_call enable always trigger tool_read_call_application_session_final;


-- ------------------------------- qualified evidence is append-only and frozen
--
-- F4: the relink triggers are UPDATE-scoped, so DELETE-then-reinsert converted a
-- legacy NULL-session row into qualified evidence by reusing its key. F6: the
-- routine write credential holds UPDATE on these tables, so a row could keep
-- asserting "session S vouched for this" while its content was rewritten
-- afterwards — reachable both directly and through ON CONFLICT DO UPDATE, which
-- sidesteps a column-scoped trigger entirely.
--
-- Both guards are deliberately scoped to rows that ARE qualified. Legacy rows
-- keep whatever retention behaviour they have today, so this migration cannot
-- break an existing cleanup path; what it refuses is the destruction or
-- rewriting of evidence that a session vouched for.

create function ops.refuse_qualified_evidence_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    if old.application_session_id is not null then
      raise exception
        'qualified evidence cannot be deleted (row is bound to application session %)',
        old.application_session_id;
    end if;
    return old;
  end if;
  if old.application_session_id is not null then
    raise exception
      'qualified evidence cannot be rewritten (row is bound to application session %)',
      old.application_session_id;
  end if;
  return new;
end $$;

create trigger tool_call_qualified_evidence_frozen
before update or delete on public.tool_call
for each row execute function ops.refuse_qualified_evidence_rewrite();
alter table public.tool_call enable always trigger tool_call_qualified_evidence_frozen;

-- DELETE only, NOT update. update-decision and detach-decision both rewrite an
-- event row in place, and detach-decision is the repo's designed "nothing is
-- deleted, the pointer is restated" retraction path. Freezing UPDATE here would
-- mean a wrongly-attached decision could never be retracted. Deletion of
-- qualified evidence stays refused.
create trigger event_qualified_evidence_frozen
before delete on public.event
for each row execute function ops.refuse_qualified_evidence_rewrite();
alter table public.event enable always trigger event_qualified_evidence_frozen;

create trigger tool_read_call_qualified_evidence_frozen
before update or delete on public.tool_read_call
for each row execute function ops.refuse_qualified_evidence_rewrite();
-- (tool_read_call has no in-place update path in the verb layer, so both apply.)
alter table public.tool_read_call enable always trigger tool_read_call_qualified_evidence_frozen;

-- --------------------------------------------------------------- privileges

revoke all on function ops.mint_application_session(
  uuid,uuid,text,text,text,text,text,text,timestamptz) from public;
revoke all on function ops.revoke_application_session(uuid,text) from public;
revoke all on function ops.application_session_is_live(uuid) from public;
revoke all on function ops.require_live_application_session() from public;
revoke all on function ops.refuse_application_session_rewrite() from public;
revoke all on function ops.refuse_application_session_relink() from public;
revoke all on function ops.refuse_qualified_evidence_rewrite() from public;

-- USAGE, not just EXECUTE. EXECUTE on a function in a schema the role cannot
-- enter is unusable: the grantee gets "permission denied for schema ops" and
-- never reaches the function at all. The whole separation argument rests on
-- carr_session_minter being able to mint, and that capability had never been
-- exercised, because the contract suite mints as the cluster superuser.
grant usage on schema ops to carr_session_minter;
grant execute on function ops.mint_application_session(
  uuid,uuid,text,text,text,text,text,text,timestamptz) to carr_session_minter;
grant execute on function ops.revoke_application_session(uuid,text) to carr_session_minter;
grant execute on function ops.application_session_is_live(uuid) to carr_writer, carr_reader;
grant select on ops.application_session to carr_reader, carr_writer;

revoke insert, update, delete on ops.application_session from carr_writer;

-- ------------------------------------------------------------------- proof
--
-- WHAT THIS BLOCK IS FOR, and what it cannot do.
--
-- The contract suite that exercises this substrate is local-only by design: it
-- needs a live PostgreSQL, and this repo's Action minutes are metered. That
-- makes THIS BLOCK the only guarantee that runs where the migration actually
-- lands. An earlier version of it checked trigger names, tables, function oids,
-- timing, and WHEN clauses — plumbing — and never checked BEHAVIOUR. A review
-- gutted every guard body to `begin return new; end`, left the plumbing intact,
-- and the block passed while the substrate accepted forged cross-actor,
-- cross-tenant evidence bound to a revoked session.
--
-- So the checks below EXERCISE the guards instead of describing them. Every
-- probe row is written inside a subtransaction that is rolled back before this
-- migration commits, so the proof leaves nothing behind.
--
-- HONEST LIMIT: this defends against a guard that is broken or gutted, which is
-- the realistic failure. It cannot defend against a body deliberately
-- backdoored to behave correctly during the probe and wrongly afterwards, nor
-- against statements appended after this block. Those are covered by the
-- sha-checking in tools/migrate.py and by review, not here. The one backdoor
-- shape cheap enough to exclude — a runtime switch — is excluded directly.

do $$
declare
  probe_actor  uuid;
  other_actor  uuid;
  probe_sid    uuid := gen_random_uuid();
  expired_sid  uuid := gen_random_uuid();
  probe_subject uuid := gen_random_uuid();
  legacy_subject uuid := gen_random_uuid();
  sentinel     text;
  legacy_key   text := 'probe-legacy-' || gen_random_uuid()::text;
  qualified_key text := 'probe-qualified-' || gen_random_uuid()::text;
  offenders    text;
begin
  ---------------------------------------------------------------- privileges
  -- Catalog ACLs, NOT has_table_privilege. That function reports false for a
  -- COLUMN-level grant, so `grant update (revoked_at) on ops.application_session
  -- to carr_writer` slipped past the previous form — and setting revoked_at is
  -- revocation, i.e. the ability to silence the fleet's evidence.
  select string_agg(format('%s:%s(%s)', grantee, privilege_type, 'column'), ', ')
    into offenders
    from information_schema.column_privileges
   where table_schema = 'ops' and table_name = 'application_session'
     and privilege_type in ('INSERT','UPDATE','DELETE')
     and grantee not in ('carr_session_minter', current_user)
     and (grantee = 'PUBLIC'
          or not exists (select 1 from pg_roles where rolname = grantee and rolsuper));
  if offenders is not null then
    raise exception '0204 FAILED: column-level grants let these roles create or '
      'mutate sessions: %', offenders;
  end if;

  select string_agg(format('%s:%s', grantee, privilege_type), ', ') into offenders
    from information_schema.table_privileges
   where table_schema = 'ops' and table_name = 'application_session'
     and privilege_type in ('INSERT','UPDATE','DELETE')
     and grantee not in ('carr_session_minter', current_user)
     and (grantee = 'PUBLIC'
          or not exists (select 1 from pg_roles where rolname = grantee and rolsuper));
  if offenders is not null then
    raise exception '0204 FAILED: these roles can create or mutate sessions, and '
      'setting revoked_at is revocation: %', offenders;
  end if;

  -- MINT and REVOKE are both swept, over the function ACL itself rather than
  -- has_function_privilege. The previous form guarded revocation across all
  -- roles but still named carr_writer alone for minting — and forging
  -- authentication evidence is strictly worse than revoking it. A grant of the
  -- mint to carr_reader passed every gate.
  select string_agg(format('%s -> %s', p.proname, a.grantee::regrole::text), ', ')
    into offenders
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
   where n.nspname = 'ops'
     and p.proname in ('mint_application_session','revoke_application_session')
     and a.privilege_type = 'EXECUTE'
     -- grantee 0 IS PUBLIC. Excluding it left the widest grantee unswept.
     -- current_user, like the two table sweeps above. `create function` makes the
     -- applying role the OWNER, and an owner's implicit EXECUTE is a real proacl
     -- entry that aclexplode returns. On Neon the migration runs as neondb_owner,
     -- which 0005_role_admin_grants.sql documents as NOT a superuser — so without
     -- this the sweep reports the migration's own owner and the file cannot apply
     -- to production at all. Every local harness missed it by creating
     -- neondb_owner as a superuser, simulating the production role shape wrongly.
     and a.grantee::regrole::text not in ('carr_session_minter', current_user)
     and (a.grantee = 0
          or not exists (select 1 from pg_roles r where r.oid = a.grantee and r.rolsuper));
  if offenders is not null then
    raise exception '0204 FAILED: only carr_session_minter may mint or revoke; found: %',
      offenders;
  end if;

  -- Membership, including NOINHERIT. has_function_privilege uses the inheriting
  -- form, so a NOINHERIT member of carr_session_minter reported no privilege and
  -- then reached the mint through SET ROLE. NOINHERIT plus SET ROLE is the
  -- normal way to wire a privilege bundle, so this is the likely shape of the
  -- door wiring a later slice adds.
  -- NO RUNTIME ROLE MAY REACH THE MINT, directly or by inheritance. This used to
  -- assert that the minter had NO members at all, which was over-tight in a way
  -- that only showed up later: wiring a door is the whole point of the next
  -- slice, and it wires one by making a dedicated issuer credential a member.
  -- The strict form made this migration refuse to apply to any fresh database in
  -- a cluster where that had already happened — role membership is cluster-wide
  -- while migrations are per-database — so an assertion meant to protect the
  -- substrate would have blocked the substrate's own intended use.
  --
  -- The property worth asserting was never "nobody is a member". It is that the
  -- credentials this substrate CONSTRAINS cannot reach the thing that mints. A
  -- purpose-built issuer being a member is the design; carr_writer being one,
  -- through any path, is the failure.
  select string_agg(format('%s (SET ROLE=%s, inherits=%s)', r,
                           pg_has_role(r, 'carr_session_minter', 'MEMBER'),
                           pg_has_role(r, 'carr_session_minter', 'USAGE')), ', ')
    into offenders
    from unnest(array['carr_writer','carr_reader','carr_jobs','carr_authority',
                      'carr_exporter','carr_device_evidence']) r
   where exists (select 1 from pg_roles where rolname = r)
     and (pg_has_role(r, 'carr_session_minter', 'MEMBER')
          or pg_has_role(r, 'carr_session_minter', 'USAGE'));
  if offenders is not null then
    raise exception '0204 FAILED: these runtime roles can reach the mint, so the '
      'credential this substrate constrains could forge its own authentication: %',
      offenders;
  end if;
  if exists (select 1 from pg_roles where rolname='carr_session_minter' and rolcanlogin) then
    raise exception '0204 FAILED: carr_session_minter must be NOLOGIN';
  end if;

  ---------------------------------------------------------------- definer shape
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                  where n.nspname='ops' and p.proname='mint_application_session'
                    and p.prosecdef) then
    raise exception '0204 FAILED: the mint must be SECURITY DEFINER';
  end if;
  -- PER FUNCTION. An `exists` over both names was satisfied by either one being
  -- pinned, while the message claimed both — so reordering a single function's
  -- search_path passed the very check whose text names reordering as the risk.
  select string_agg(p.proname, ', ') into offenders
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops'
     and p.proname in ('mint_application_session','revoke_application_session',
                       'require_live_application_session',
                       'refuse_application_session_rewrite',
                       'refuse_application_session_relink',
                       'refuse_qualified_evidence_rewrite')
     and not ('search_path=pg_catalog, ops, public' = any(coalesce(p.proconfig, '{}')));
  if offenders is not null then
    raise exception '0204 FAILED: these functions do not pin search_path to exactly '
      'pg_catalog, ops, public (a different ORDER is a different function): %', offenders;
  end if;
  -- A guard has no business reading a runtime setting, so the cheapest and most
  -- likely switch is excluded across ALL FOUR guard bodies, with comments stripped
  -- first (a comment mentioning the function name used to fail the migration).
  --
  -- Block comments are NOT stripped below, only line comments, so a future
  -- block comment containing one of these words fails the migration.
  --
  -- THIS IS NOT AN ANTI-BACKDOOR CONTROL, and it must not be read as one. A
  -- review defeated the single-identifier version five ways in an afternoon,
  -- including assembling the identifier at runtime and keying the bypass on
  -- session_user. A body written to behave during this proof and misbehave after
  -- it cannot be caught from inside the migration that ships it; the checksums in
  -- tools/migrate.py and human review are the controls for that. What this block
  -- genuinely catches is a guard that is BROKEN or GUTTED, which is the failure
  -- that has actually happened here twice. A review defeated an earlier version
  -- of this list with a time bomb and with the identifier assembled from
  -- fragments; neither is caught, and the claim is not that they are.
  select string_agg(p.proname, ', ') into offenders
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops'
     and p.proname in ('require_live_application_session',
                       'refuse_application_session_rewrite',
                       'refuse_application_session_relink',
                       'refuse_qualified_evidence_rewrite')
     -- The pattern is parenthesised: ~* binds tighter than ||, so without them
     -- the whole expression collapses to text and the statement fails to parse.
     and regexp_replace(p.prosrc, '--[^\n]*', '', 'g') ~* (
         -- a runtime switch
         'current_setting|set_config|pg_settings'
         -- who is calling: a guard must behave the same for every caller, and a
         -- bypass keyed on session_user is invisible to both harnesses here,
         -- since each connects as a superuser and reaches carr_writer by SET ROLE
         || '|session_user|current_user|pg_roles|pg_authid'
         -- whether the migration has finished: the ledger row is written AFTER
         -- this block runs, so a guard keyed on it is armed for exactly the
         -- duration of its own proof
         || '|schema_migrations|txid_current|to_regclass'
         -- dynamic SQL, which is how an excluded identifier gets assembled at
         -- runtime out of fragments this pattern cannot see
         || '|execute\s+');
  if offenders is not null then
    raise exception '0204 FAILED: these guards reference something a guard has no '
      'business reading — a runtime setting, the calling role, migration state, or '
      'dynamic SQL. Each is a way to behave correctly during this proof and '
      'differently afterwards: %', offenders;
  end if;

  ---------------------------------------------------------------- foreign keys
  if (select count(*) from pg_constraint c
       where c.contype = 'f'
         and c.confrelid = 'ops.application_session'::regclass
         and c.conrelid in ('public.tool_call'::regclass, 'public.event'::regclass,
                            'public.tool_read_call'::regclass)) <> 3 then
    raise exception '0204 FAILED: each audit table must carry a foreign key to '
      'ops.application_session';
  end if;

  ---------------------------------------------------------------- trigger shape
  -- Behaviour alone cannot see this. A trigger left ENABLE ORIGIN behaves
  -- exactly like an ENABLE ALWAYS one during these probes and is switched off
  -- wholesale by session_replication_role='replica' afterwards; a missing
  -- trigger on a table no probe happens to touch is likewise invisible. Shape
  -- and behaviour are both checked because neither subsumes the other.
  declare
    expected constant text[][] := array[
      ['application_session_identity_immutable','ops.application_session','ops.refuse_application_session_rewrite','BEFORE DELETE OR UPDATE',''],
      ['tool_call_requires_live_session','public.tool_call','ops.require_live_application_session','BEFORE INSERT',''],
      ['event_requires_live_application_session','public.event','ops.require_live_application_session','BEFORE INSERT',''],
      ['tool_read_call_requires_live_session','public.tool_read_call','ops.require_live_application_session','BEFORE INSERT',''],
      ['tool_call_application_session_final','public.tool_call','ops.refuse_application_session_relink','UPDATE OF APPLICATION_SESSION_ID',''],
      ['event_application_session_final','public.event','ops.refuse_application_session_relink','UPDATE OF APPLICATION_SESSION_ID',''],
      ['tool_read_call_application_session_final','public.tool_read_call','ops.refuse_application_session_relink','UPDATE OF APPLICATION_SESSION_ID',''],
      ['tool_call_qualified_evidence_frozen','public.tool_call','ops.refuse_qualified_evidence_rewrite','BEFORE DELETE OR UPDATE',''],
      ['event_qualified_evidence_frozen','public.event','ops.refuse_qualified_evidence_rewrite','BEFORE DELETE','UPDATE'],
      ['tool_read_call_qualified_evidence_frozen','public.tool_read_call','ops.refuse_qualified_evidence_rewrite','BEFORE DELETE OR UPDATE','']];
    i int;
    hit record;
  begin
    for i in 1 .. array_length(expected, 1) loop
      select t.tgname, t.tgenabled, t.tgqual, pg_get_triggerdef(t.oid) as def into hit
        from pg_trigger t
       where not t.tgisinternal
         and t.tgname  = expected[i][1]
         and t.tgrelid = expected[i][2]::regclass
         and t.tgfoid  = expected[i][3]::regproc;
      if not found then
        raise exception '0204 FAILED: trigger % is missing, or is not attached to % '
          'running %', expected[i][1], expected[i][2], expected[i][3];
      end if;
      if hit.tgenabled <> 'A' then
        raise exception '0204 FAILED: trigger % on % is not ENABLE ALWAYS',
          expected[i][1], expected[i][2];
      end if;
      if position('FOR EACH ROW' in upper(hit.def)) = 0 then
        raise exception '0204 FAILED: trigger % on % is not FOR EACH ROW',
          expected[i][1], expected[i][2];
      end if;
      if expected[i][3] = 'ops.refuse_application_session_relink' then
        -- IS DISTINCT FROM is symmetric, so accept either operand order rather
        -- than failing a trigger that is identical in meaning. What must not pass
        -- is an ADDED term ("... and false"), which the length check catches.
        if not (upper(hit.def) like '%WHEN ((OLD.APPLICATION_SESSION_ID IS DISTINCT FROM NEW.APPLICATION_SESSION_ID))%'
             or upper(hit.def) like '%WHEN ((NEW.APPLICATION_SESSION_ID IS DISTINCT FROM OLD.APPLICATION_SESSION_ID))%') then
          raise exception '0204 FAILED: relink trigger % does not carry exactly the '
            'expected WHEN condition; it may be inert. Definition: %',
            expected[i][1], hit.def;
        end if;
      elsif hit.tgqual is not null then
        raise exception '0204 FAILED: trigger % on % carries a WHEN clause it must not '
          'have; a condition that never holds makes it inert', expected[i][1], expected[i][2];
      end if;
      if position(expected[i][4] in upper(hit.def)) = 0 then
        raise exception '0204 FAILED: trigger % on % is the wrong shape (% absent). '
          'Definition: %', expected[i][1], expected[i][2], expected[i][4], hit.def;
      end if;
      if expected[i][5] <> '' and position(expected[i][5] in upper(hit.def)) > 0 then
        raise exception '0204 FAILED: trigger % on % must NOT cover %',
          expected[i][1], expected[i][2], expected[i][5];
      end if;
    end loop;
  end;

  ---------------------------------------------------------------- BEHAVIOUR
  select id into probe_actor from public.actor order by slug limit 1;
  select id into other_actor from public.actor where id <> probe_actor order by slug limit 1;
  if probe_actor is null or other_actor is null then
    raise exception '0204 FAILED: cannot prove the guards without two actor rows';
  end if;

  -- The rollback sentinel carries a value generated HERE, at apply time. It used
  -- to be the fixed string 'PROBE_ROLLBACK', and a guard that raised that text
  -- during the FIRST probe made the outer handler treat the abort as the normal
  -- rollback path — every later probe went unexecuted, the residue check passed
  -- trivially because nothing had been written, and the migration committed over
  -- a substrate that accepted revoked, cross-actor, cross-tenant evidence. A
  -- guard cannot raise a string it cannot know.
  sentinel := 'PROBE_ROLLBACK_' || gen_random_uuid()::text;

  begin
    perform ops.mint_application_session(probe_sid, probe_actor, 'carr-internal',
      null, 'probe', 'probe-issuer', 'probe-class', 'probe-subject',
      clock_timestamp() + interval '5 minutes');
    perform ops.mint_application_session(expired_sid, probe_actor, 'carr-internal',
      null, 'probe', 'probe-issuer', 'probe-class', 'probe-subject',
      clock_timestamp() + interval '1 second');

    if not ops.application_session_is_live(probe_sid) then
      raise exception '0204 FAILED: a freshly minted session is not live';
    end if;

    -- POSITIVE CASES FIRST, on ALL THREE tables. A substrate that refuses
    -- everything would sail through a suite of negative probes while producing
    -- no evidence at all, which is the silent failure this file exists to stop.
    -- AS THE RUNTIME WRITER, not as the migration owner. The owner holds
    -- privileges carr_writer does not, so a positive probe run as the owner
    -- proves the guard works for somebody who was never going to be blocked.
    --
    -- This is not a hypothetical. Removing SECURITY DEFINER from the binding
    -- guard makes it take a row lock as the caller, row locks require UPDATE,
    -- and UPDATE on the session table is exactly what the writer is denied — so
    -- every qualified insert is refused while the legacy path still succeeds and
    -- the fleet quietly produces nothing that qualifies. Run as the owner, that
    -- passes. The contract suite catches it, and the contract suite deliberately
    -- does not run in CI, which leaves this block as the only place it can be
    -- caught where the migration actually lands.
    set local role carr_writer;
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (qualified_key, 'probe', probe_actor, 'h', '{}'::jsonb, 'carr-internal', probe_sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      cause, organization_tenant_id, application_session_id)
    values (now(), probe_actor, 'probe', 'decision', probe_subject, 'human_stated',
            'carr-internal', probe_sid);
    insert into public.tool_read_call (verb, actor_slug, actor_id,
      organization_tenant_id, application_session_id)
    values ('probe', (select slug from public.actor where id = probe_actor),
            probe_actor, 'carr-internal', probe_sid);
    reset role;
    if (select count(*) from public.tool_call where application_session_id = probe_sid) <> 1
       or (select count(*) from public.event where application_session_id = probe_sid) <> 1
       or (select count(*) from public.tool_read_call where application_session_id = probe_sid) <> 1 then
      raise exception '0204 FAILED: a qualified insert reported success but wrote no row';
    end if;

    -- LEGACY rows on both tables, so the promotion probes have a real target.
    -- An UPDATE that matches nothing raises nothing and would read as "accepted",
    -- which is a probe that tests its own fixture rather than the guard.
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id)
    values (legacy_key, 'probe', probe_actor, 'h', '{}'::jsonb, 'carr-internal');
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      cause, organization_tenant_id)
    values (now(), probe_actor, 'probe', 'decision', legacy_subject, 'human_stated',
            'carr-internal');

    -- The expired probe needs the expiry to have actually passed. The whole
    -- proof runs inside one fast transaction, so without this wait the session
    -- minted a second out is still live when the probe runs, and the probe fails
    -- for the wrong reason. One second, once, at migration time.
    perform pg_sleep(1.2);

    -- NEGATIVE CASES. Each probe names the message its guard must raise. Asserting
    -- only "something failed" is how a probe scores a pass while testing nothing:
    -- a typo in a column name raises 42703 and reads as a refusal, and a deleted
    -- guard can be masked by a DIFFERENT guard refusing for its own reason. The
    -- contract suite has stated this as a rule since round three; the gate that
    -- actually runs in production did not implement it.
    declare
      probes text[][] := array[
        ['unknown application session', 'tool_call unknown session',
         format('insert into public.tool_call (idempotency_key,verb,actor_id,request_hash,response,organization_tenant_id,application_session_id) values (%L,%L,%L,%L,%L,%L,%L)',
                'p'||gen_random_uuid()::text,'probe',probe_actor,'h','{}','carr-internal',gen_random_uuid())],
        ['different actor', 'tool_call cross-actor',
         format('insert into public.tool_call (idempotency_key,verb,actor_id,request_hash,response,organization_tenant_id,application_session_id) values (%L,%L,%L,%L,%L,%L,%L)',
                'p'||gen_random_uuid()::text,'probe',other_actor,'h','{}','carr-internal',probe_sid)],
        ['different tenant', 'tool_call cross-tenant',
         format('insert into public.tool_call (idempotency_key,verb,actor_id,request_hash,response,organization_tenant_id,application_session_id) values (%L,%L,%L,%L,%L,%L,%L)',
                'p'||gen_random_uuid()::text,'probe',probe_actor,'h','{}','other-tenant',probe_sid)],
        ['is expired', 'tool_call expired session',
         format('insert into public.tool_call (idempotency_key,verb,actor_id,request_hash,response,organization_tenant_id,application_session_id) values (%L,%L,%L,%L,%L,%L,%L)',
                'p'||gen_random_uuid()::text,'probe',probe_actor,'h','{}','carr-internal',expired_sid)],
        ['different actor', 'event cross-actor',
         format('insert into public.event (occurred_at,actor_id,verb,subject_type,subject_id,cause,organization_tenant_id,application_session_id) values (now(),%L,%L,%L,%L,%L,%L,%L)',
                other_actor,'probe','decision',gen_random_uuid(),'human_stated','carr-internal',probe_sid)],
        ['unknown application session', 'event unknown session',
         format('insert into public.event (occurred_at,actor_id,verb,subject_type,subject_id,cause,organization_tenant_id,application_session_id) values (now(),%L,%L,%L,%L,%L,%L,%L)',
                probe_actor,'probe','decision',gen_random_uuid(),'human_stated','carr-internal',gen_random_uuid())],
        ['different tenant', 'event cross-tenant',
         format('insert into public.event (occurred_at,actor_id,verb,subject_type,subject_id,cause,organization_tenant_id,application_session_id) values (now(),%L,%L,%L,%L,%L,%L,%L)',
                probe_actor,'probe','decision',gen_random_uuid(),'human_stated','other-tenant',probe_sid)],
        ['different tenant', 'tool_read_call cross-tenant',
         format('insert into public.tool_read_call (verb,actor_slug,organization_tenant_id,application_session_id) values (%L,%L,%L,%L)',
                'probe',(select slug from public.actor where id=probe_actor),'other-tenant',probe_sid)],
        ['does not belong to actor', 'tool_read_call cross-slug',
         format('insert into public.tool_read_call (verb,actor_slug,organization_tenant_id,application_session_id) values (%L,%L,%L,%L)',
                'probe',(select slug from public.actor where id=other_actor),'carr-internal',probe_sid)],
        ['different actor', 'tool_read_call cross-actor-id',
         format('insert into public.tool_read_call (verb,actor_slug,actor_id,organization_tenant_id,application_session_id) values (%L,%L,%L,%L,%L)',
                'probe',(select slug from public.actor where id=probe_actor),other_actor,'carr-internal',probe_sid)],
        ['identity is immutable', 'session identity rewrite',
         format('update ops.application_session set actor_id=%L where id=%L', other_actor, probe_sid)],
        ['cannot be deleted', 'session deletion',
         format('delete from ops.application_session where id=%L', probe_sid)],
        ['cannot be rewritten', 'qualified tool_call rewrite',
         format('update public.tool_call set request_hash=%L where idempotency_key=%L','X',qualified_key)],
        ['cannot be deleted', 'qualified tool_call deletion',
         format('delete from public.tool_call where idempotency_key=%L', qualified_key)],
        ['cannot be deleted', 'qualified event deletion',
         format('delete from public.event where subject_id=%L', probe_subject)],
        ['cannot be changed', 'legacy promotion',
         format('update public.tool_call set application_session_id=%L where idempotency_key=%L', probe_sid, legacy_key)],
        ['cannot be changed', 'legacy event promotion',
         format('update public.event set application_session_id=%L where subject_id=%L', probe_sid, legacy_subject)],
        ['may not exceed', 'unbounded lifetime',
         format('select ops.mint_application_session(%L,%L,%L,null,%L,%L,%L,%L,%L)',
                gen_random_uuid(),probe_actor,'carr-internal','probe','i','c','s', now()+interval '400 days')],
        ['no such application session', 'revoking an unknown session',
         format('select ops.revoke_application_session(%L,%L)', gen_random_uuid(), 'probe')]
      ];
      k int;
      ran int := 0;
    begin
      for k in 1 .. array_length(probes, 1) loop
        begin
          execute probes[k][3];
          raise exception '0204 FAILED: % was ACCEPTED but every guard here exists to '
            'refuse it. The plumbing may be present while the logic is not.', probes[k][2];
        exception
          when others then
            if sqlerrm like '0204 FAILED%' then raise; end if;
            if sqlerrm like 'PROBE_ROLLBACK%' then raise; end if;
            if position(lower(probes[k][1]) in lower(sqlerrm)) = 0 then
              raise exception '0204 FAILED: % was refused, but by a DIFFERENT guard than '
                'the one under test. Expected a message containing %; got: %',
                probes[k][2], probes[k][1], sqlerrm;
            end if;
            ran := ran + 1;
        end;
      end loop;
      -- Every probe must have RUN. A guard able to abort the loop early would
      -- otherwise leave the rest unexecuted and unnoticed.
      if ran <> array_length(probes, 1) then
        raise exception '0204 FAILED: only % of % behaviour probes completed',
          ran, array_length(probes, 1);
      end if;
    end;

    -- The freeze guard must be scoped to QUALIFIED rows. An unconditional raise
    -- satisfies every negative freeze probe while breaking this file's own stated
    -- scope ("legacy rows keep whatever retention behaviour they have today"), so
    -- the positive case is what actually pins it.
    -- UPDATE the legacy tool_call FIRST. Updating a legacy event proves nothing:
    -- the event freeze trigger is DELETE-only, so it never fires on an update and
    -- an over-broad update guard sails past. tool_call carries both arms.
    update public.tool_call set request_hash = 'still-legacy'
     where idempotency_key = legacy_key;
    if not exists (select 1 from public.tool_call
                    where idempotency_key = legacy_key and request_hash = 'still-legacy') then
      raise exception '0204 FAILED: a legacy row could not be updated — the freeze '
        'guard is not scoped to qualified rows and will break existing writers';
    end if;
    delete from public.tool_call where idempotency_key = legacy_key;
    if exists (select 1 from public.tool_call where idempotency_key = legacy_key) then
      raise exception '0204 FAILED: a legacy row could not be removed — the freeze '
        'guard is not scoped to qualified rows and will break existing retention';
    end if;
    delete from public.event where subject_id = legacy_subject;

    -- F5: liveness must actually consider expiry. The only probes were "fresh is
    -- live" and "revoked is not live", so a function ignoring expires_at passed.
    if not ops.application_session_is_live(probe_sid) then
      raise exception '0204 FAILED: a live session reports not live';
    end if;
    if ops.application_session_is_live(expired_sid) then
      raise exception '0204 FAILED: an EXPIRED session reports live — the liveness '
        'function ignores expires_at, and it is the API the layer above consumes';
    end if;

    -- F11: a revocation with no stated reason is not a revocation record.
    begin
      perform ops.revoke_application_session(expired_sid, '');
      raise exception '0204 FAILED: revoking with an empty reason reported success';
    exception
      when others then
        if sqlerrm like '0204 FAILED%' then raise; end if;
        if position('requires a reason' in lower(sqlerrm)) = 0 then
          raise exception '0204 FAILED: the empty-reason refusal came from a '
            'different guard: %', sqlerrm;
        end if;
    end;

    perform ops.revoke_application_session(probe_sid, 'probe complete');
    if ops.application_session_is_live(probe_sid) then
      raise exception '0204 FAILED: a revoked session still reports live';
    end if;
    begin
      insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
        response, organization_tenant_id, application_session_id)
      values ('p'||gen_random_uuid()::text,'probe',probe_actor,'h','{}'::jsonb,
              'carr-internal', probe_sid);
      raise exception '0204 FAILED: a REVOKED session still produced qualified evidence';
    exception
      when others then
        if sqlerrm like '0204 FAILED%' then raise; end if;
        if position('is revoked' in lower(sqlerrm)) = 0 then
          raise exception '0204 FAILED: the revoked-session refusal came from a '
            'different guard: %', sqlerrm;
        end if;
    end;
    -- Revocation must bite on ALL THREE tables, not just the one probed above.
    declare
      after_revoke text[][] := array[
        ['event',
         format('insert into public.event (occurred_at,actor_id,verb,subject_type,subject_id,cause,organization_tenant_id,application_session_id) values (now(),%L,%L,%L,%L,%L,%L,%L)',
                probe_actor,'probe','decision',gen_random_uuid(),'human_stated','carr-internal',probe_sid)],
        ['tool_read_call',
         format('insert into public.tool_read_call (verb,actor_slug,organization_tenant_id,application_session_id) values (%L,%L,%L,%L)',
                'probe',(select slug from public.actor where id=probe_actor),'carr-internal',probe_sid)]
      ];
      j int;
    begin
      for j in 1 .. array_length(after_revoke, 1) loop
        begin
          execute after_revoke[j][2];
          raise exception '0204 FAILED: % accepted evidence for a REVOKED session',
            after_revoke[j][1];
        exception
          when others then
            if sqlerrm like '0204 FAILED%' then raise; end if;
            if position('is revoked' in lower(sqlerrm)) = 0 then
              raise exception '0204 FAILED: % refused a revoked session by a different '
                'guard: %', after_revoke[j][1], sqlerrm;
            end if;
        end;
      end loop;
    end;

    begin
      perform ops.revoke_application_session(probe_sid, 'second');
      raise exception '0204 FAILED: re-revoking reported success';
    exception
      when others then
        if sqlerrm like '0204 FAILED%' then raise; end if;
        if position('already revoked' in lower(sqlerrm)) = 0 then
          raise exception '0204 FAILED: the re-revocation refusal came from a '
            'different guard: %', sqlerrm;
        end if;
    end;

    raise exception '%', sentinel;   -- discard every probe row
  exception
    when others then
      if sqlerrm like '0204 FAILED%' then raise; end if;
      if sqlerrm <> sentinel then
        raise exception '0204 FAILED: the behaviour proof could not complete: %', sqlerrm;
      end if;
  end;

  if exists (select 1 from ops.application_session where id in (probe_sid, expired_sid))
     or exists (select 1 from public.tool_call
                 where idempotency_key in (qualified_key, legacy_key))
     or exists (select 1 from public.event where subject_id in (probe_subject, legacy_subject)) then
    raise exception '0204 FAILED: the behaviour proof left rows behind';
  end if;
end $$;

commit;
