-- Program 6 browser approvals consume an opaque, server-issued challenge once.
--
-- This is deliberately a tiny authority-only ledger rather than a workflow
-- engine.  It remembers only digests; the browser token, session identifier,
-- and approved material never enter the record layer.  A redemption records no
-- execution, dispatch, completion, release, or Work Request state change.

begin;

create table ops.program6_browser_action_challenge_redemption (
  id                uuid primary key default gen_random_uuid(),
  token_digest      text not null unique check (token_digest ~ '^[0-9a-f]{64}$'),
  session_digest    text not null check (session_digest ~ '^[0-9a-f]{64}$'),
  action            text not null check (action in ('accept-ready-plan','accept-outcome-feedback')),
  material_digest   text not null check (material_digest ~ '^[0-9a-f]{64}$'),
  idempotency_key   uuid not null,
  redeemed_by_actor_id uuid not null references public.actor(id),
  redeemed_at       timestamptz not null default clock_timestamp()
);

comment on table ops.program6_browser_action_challenge_redemption is
  'Private append-only one-time browser approval-challenge redemption ledger. It holds SHA-256 digests only, is authority-session attributed, and grants no execution or lifecycle authority.';

create or replace function ops.program6_browser_action_challenge_redemptions_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'Program 6 browser action challenge redemptions are append-only';
end;
$$;

create trigger program6_browser_action_challenge_redemptions_immutable
before update or delete on ops.program6_browser_action_challenge_redemption
for each row execute function ops.program6_browser_action_challenge_redemptions_immutable();

create or replace function ops.redeem_program6_browser_action_challenge(
  p_token_digest text,
  p_session_digest text,
  p_action text,
  p_material_digest text,
  p_idempotency_key uuid
)
returns boolean
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor_slug text;
  v_actor public.actor%rowtype;
  v_inserted_id uuid;
begin
  if coalesce(p_token_digest, '') !~ '^[0-9a-f]{64}$'
     or coalesce(p_session_digest, '') !~ '^[0-9a-f]{64}$'
     or coalesce(p_material_digest, '') !~ '^[0-9a-f]{64}$'
     or coalesce(p_action, '') not in ('accept-ready-plan','accept-outcome-feedback')
     or p_idempotency_key is null then
    raise exception 'browser approval challenge requires lowercase SHA-256 token, session, and material digests, one approved action, and a UUID idempotency key';
  end if;

  v_actor_slug := ops.authority_actor_slug();
  select a.* into v_actor
    from public.actor a
   where a.slug = v_actor_slug
     and a.active
     and a.kind = 'human'
   for share;
  if not found then
    raise exception 'authority session user is not an active human actor';
  end if;

  -- The token's unique index makes concurrent redemption atomic.  A token
  -- that has already been consumed is an ordinary false result, never a
  -- second authority action and never a mutation of the first audit fact.
  insert into ops.program6_browser_action_challenge_redemption
    (token_digest,session_digest,action,material_digest,idempotency_key,redeemed_by_actor_id)
  values
    (p_token_digest,p_session_digest,p_action,p_material_digest,p_idempotency_key,v_actor.id)
  on conflict (token_digest) do nothing
  returning id into v_inserted_id;

  return v_inserted_id is not null;
end;
$$;

revoke all on table ops.program6_browser_action_challenge_redemption
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.program6_browser_action_challenge_redemptions_immutable()
  from public;
revoke all on function ops.redeem_program6_browser_action_challenge(text,text,text,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.redeem_program6_browser_action_challenge(text,text,text,text,uuid)
  to carr_authority;

commit;

do $$
declare
  v_definition text;
begin
  if has_table_privilege('carr_writer', 'ops.program6_browser_action_challenge_redemption', 'INSERT')
     or has_table_privilege('carr_authority', 'ops.program6_browser_action_challenge_redemption', 'INSERT')
     or has_function_privilege('carr_writer',
          'ops.redeem_program6_browser_action_challenge(text,text,text,text,uuid)'::regprocedure,
          'EXECUTE') then
    raise exception '0183 FAILED: browser challenge redemption is directly forgeable';
  end if;
  select pg_get_functiondef(
    'ops.redeem_program6_browser_action_challenge(text,text,text,text,uuid)'::regprocedure
  ) into v_definition;
  if v_definition not like '%authority_actor_slug()%'
     or v_definition not like '%on conflict (token_digest) do nothing%'
     or v_definition not like '%return v_inserted_id is not null%'
     or v_definition like '%p_actor%' then
    raise exception '0183 FAILED: browser challenge redemption is not authority-bound atomic one-time consumption';
  end if;
end;
$$;
