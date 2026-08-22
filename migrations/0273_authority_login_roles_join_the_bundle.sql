-- 0273_authority_login_roles_join_the_bundle.sql
--
-- THE SYMPTOM, reported live on 2026-08-22 after the first rule activation in
-- two days finally succeeded: approve-rule works with a full 36-character rule
-- id and fails with the 8-character short id that standing-context actually
-- prints. Short ids are what a session has and what a partner would paste, so
-- the working form was the one nobody uses.
--
-- THE CAUSE. approve-rule resolves a short id by prefix — `select id from rule
-- where id::text like $1 || '%'` in resolveRuleId (mcp-server/src/tools.js) —
-- and that read happens on the AUTHORITY connection, which authenticates as the
-- externally provisioned login role carr_authority_joe or carr_authority_dell.
-- 0161 built carr_authority as a NOLOGIN privilege bundle and says so in its own
-- header: "this migration supplies only the NOLOGIN privilege bundle and derives
-- the actor from session_user". The bundle holds `grant select on table
-- public.rule`. The login roles were never made members of it.
--
-- So the prefix lookup raised 42501 insufficient_privilege, the full-uuid path
-- skipped the lookup entirely and worked, and the difference looked like an
-- id-format quirk rather than a missing grant. It reached the caller as a bare
-- "internal error" besides, which is why it took a live activation to find —
-- that masking is fixed separately in PR 465.
--
-- WHY MEMBERSHIP RATHER THAN A DIRECT GRANT. The bundle is the design. 0161
-- deliberately puts every authority privilege on one NOLOGIN role so the two
-- human login roles carry no privileges of their own and cannot drift apart from
-- each other. Granting `select on rule` straight to carr_authority_joe would fix
-- today's symptom and start exactly the drift the bundle exists to prevent —
-- and the next missing privilege would be found the same expensive way.
--
-- WHY IT IS GUARDED. carr_authority_joe and carr_authority_dell are provisioned
-- outside this repository, in the database provider's console, and Dell's may
-- legitimately not exist yet (the control-plane contract marks his authority
-- login optional_nonblocking). A migration that assumed both would fail on a
-- database where one is absent, so each is granted only if present and the
-- absence is announced rather than passed over in silence — rule 88e9b5eb: "not
-- authorized" and "not possible" are different findings.
--
-- Idempotent: re-granting an existing membership is a no-op in PostgreSQL, so
-- this is safe to re-apply and safe on a database where it already holds.

begin;

do $$
declare
  login_role text;
  granted    int := 0;
  absent     text[] := '{}';
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_authority') then
    raise exception 'carr_authority privilege bundle is missing; migration 0161 has not been applied';
  end if;

  foreach login_role in array array['carr_authority_joe', 'carr_authority_dell'] loop
    if exists (select 1 from pg_roles where rolname = login_role) then
      execute format('grant carr_authority to %I', login_role);
      granted := granted + 1;
      raise notice 'granted the carr_authority bundle to %', login_role;
    else
      absent := absent || login_role;
      raise notice 'login role % is not provisioned on this database; skipped', login_role;
    end if;
  end loop;

  if granted = 0 then
    -- REPORTED, NOT RAISED, and this was wrong in the first version of this
    -- migration: it raised, and the hosted CI run refused with its own message.
    -- Correctly, too — a throwaway CI database has neither login role, because
    -- they are provisioned in the database provider's console and no migration
    -- can create one. Refusing there would mean no branch could ever merge.
    --
    -- The companion check is ops/authority-privilege-gate.py, which takes
    -- exactly this position: a database with no authority login role is
    -- reported, while a role that EXISTS and cannot do its job is a failure.
    -- The dangerous state is a half-provisioned principal, not an absent one,
    -- and an absent one is loud by other means: no rule can be approved at all.
    raise notice
      'no authority login role exists on this database, so nothing was granted. '
      'Expected on a throwaway CI database; on Production it means '
      'carr_authority_joe is unprovisioned and no rule can be approved.';
  end if;

  if array_length(absent, 1) is not null then
    raise notice 'absent and deliberately skipped: %', array_to_string(absent, ', ');
  end if;
end $$;

-- PROOF, inside the transaction: whichever authority login roles exist must now
-- be able to read the rule table, which is the read approve-rule's short-id
-- resolution actually performs. Asserted through has_table_privilege rather than
-- by attempting the select, because that function answers for a role other than
-- the one connected.
do $$
declare login_role text;
begin
  foreach login_role in array array['carr_authority_joe', 'carr_authority_dell'] loop
    if exists (select 1 from pg_roles where rolname = login_role) then
      if not has_table_privilege(login_role, 'public.rule', 'SELECT') then
        raise exception
          '% still cannot select from public.rule after joining the bundle; the '
          'grant on the bundle itself is missing', login_role;
      end if;
    end if;
  end loop;
end $$;

commit;
