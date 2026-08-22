-- 0259 — mint a session from an actor SLUG, so the door needs no actor id
--
-- THE DEFECT THIS FIXES, FOUND BY REVIEW BEFORE ANY OF IT SHIPPED. The first
-- attempt to wire the OAuth door called ops.mint_application_session, which
-- takes an actor UUID. The door does not have one: actorFromProps builds an
-- actor from grant props carrying a slug, and actor.id is not resolved until
-- callTool runs, long after authentication. The door therefore minted nothing,
-- on every request, and every test passed because the tests supplied a
-- hand-written actor carrying an id that no door can produce.
--
-- THE REPAIR THAT LOOKS OBVIOUS IS THE WRONG ONE. Granting carr_session_issuer
-- SELECT on public.actor would let the door resolve the id itself, and it would
-- work. It also widens the one credential whose entire purpose is to hold
-- nothing but the mint. 0258 argues that the issuer must be unable to do
-- anything except mint; handing it a table read to fix an ergonomics problem
-- spends that argument for convenience.
--
-- So the resolution moves INSIDE a SECURITY DEFINER function instead. The
-- function runs as its owner, reads public.actor as the owner, and the issuer
-- still holds no table privilege of any kind. The credential gets strictly more
-- useful without getting more powerful, which is the trade worth making.
--
-- AN UNKNOWN SLUG RAISES rather than minting a session with a null actor. A
-- session whose actor is unknown would satisfy "a row exists" while failing the
-- only thing the row is for: 0257's guard matches the evidence row's actor
-- against the session's, and null matches nothing, so such a session would
-- silently make every write it vouched for non-qualifying.

create function ops.mint_application_session_for_slug(
  p_id                     uuid,
  p_actor_slug             text,
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
declare
  v_actor_id uuid;
begin
  if p_actor_slug is null or length(btrim(p_actor_slug)) = 0 then
    raise exception 'an application session requires an actor slug';
  end if;
  select id into v_actor_id from public.actor where slug = p_actor_slug;
  if v_actor_id is null then
    -- The door already checked this slug against its own allow-list, so this
    -- means the actor row was never provisioned. Say which slug, because the
    -- alternative is an authenticated caller whose writes silently stop
    -- qualifying with nothing anywhere naming the cause.
    raise exception 'no actor row for slug %; the session cannot name a principal',
      p_actor_slug;
  end if;
  -- Delegates rather than duplicating: every invariant 0257 enforces on the
  -- insert (the 30-day ceiling, the server-clock authentication instant, the
  -- immutability triggers) stays in exactly one place. A copy of that INSERT
  -- here would be a second definition of the same rules, free to drift.
  return ops.mint_application_session(
    p_id, v_actor_id, p_organization_tenant_id, p_sponsoring_human_slug,
    p_via, p_auth_issuer, p_authorization_class, p_verified_subject, p_expires_at);
end $$;

revoke all on function ops.mint_application_session_for_slug(
  uuid,text,text,text,text,text,text,text,timestamptz) from public;
grant execute on function ops.mint_application_session_for_slug(
  uuid,text,text,text,text,text,text,text,timestamptz) to carr_session_minter;

do $$
declare
  offenders text;
begin
  -- Same sweep 0257 runs over its own functions, for the same reason: a grant
  -- to anything the runtime can reach turns the separation back off.
  select string_agg(a.grantee::regrole::text, ', ') into offenders
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
   where n.nspname = 'ops'
     and p.proname = 'mint_application_session_for_slug'
     and a.privilege_type = 'EXECUTE'
     and a.grantee::regrole::text not in ('carr_session_minter', current_user)
     and (a.grantee = 0
          or not exists (select 1 from pg_roles r where r.oid = a.grantee and r.rolsuper));
  if offenders is not null then
    raise exception '0259 FAILED: only carr_session_minter may mint by slug; found: %',
      offenders;
  end if;

  -- The delegation must actually reach 0257's function, or this migration has
  -- created a mint that enforces none of 0257's invariants.
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
     where n.nspname='ops' and p.proname='mint_application_session_for_slug'
       and p.prosrc like '%ops.mint_application_session(%')
  then
    raise exception '0259 FAILED: the slug wrapper does not delegate to '
                    'ops.mint_application_session, so 0257''s invariants are bypassed';
  end if;
end $$;
