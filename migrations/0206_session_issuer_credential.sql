-- 0206 — the credential that may mint an authenticated application session
--
-- WHAT 0204 LEFT OPEN, DELIBERATELY. 0204 created carr_session_minter NOLOGIN
-- and memberless, and said so in its own comment: "Nothing can mint until a
-- later slice decides which door credential joins this role; until then the
-- substrate is inert by construction." This migration is that decision. It is
-- not a defect being cleared on the way past — choosing the credential IS the
-- work, because the choice is what makes the separation real or decorative.
--
-- THE RULE THE CHOICE HAS TO SATISFY. The minting credential must be one the
-- TOOL-EXECUTION PATH DOES NOT HOLD. Verb execution connects as the routine
-- writer (DATABASE_URL_WRITER), and the two authority verbs connect as a
-- human-bound authority identity (CARR_DB_AUTHORITY_*, see authorityDsnForActor
-- in mcp-server/src/mcp.js). If either of those could mint, then a bug anywhere
-- in the verb layer — or a leaked writer credential — manufactures an
-- authenticated session, and every guarantee 0204 enforces is enforced against
-- an attacker who can simply issue themselves a session first.
--
-- So: a NEW role, carr_session_issuer, LOGIN, whose only privilege beyond
-- connecting is membership in carr_session_minter. The authentication layer
-- holds its connection string and uses it at token and cookie issuance only.
-- Nothing in tools.js or the verb dispatch path may import it.
--
-- HONEST LIMIT, AND IT IS NOT SMALL. One Worker holding both connection strings
-- means a code-execution compromise of the Worker gets both. This separation
-- defends against a verb-layer bug reaching the mint; it does not defend
-- against Worker compromise. Anyone summarising this migration who drops that
-- sentence has made it sound stronger than it is.
--
-- WHY LOGIN, WHEN carr_session_minter IS NOT. carr_session_minter is a
-- privilege bundle and stays unreachable by password, so the mint is reachable
-- only by a role someone already holds. carr_session_issuer is the credential
-- itself, so it must be able to connect. The two are separate on purpose: the
-- bundle can be re-pointed at a different issuer later without the mint grant
-- ever being attached to something that logs in with a password of its own.

do $$
declare
  placeholder text;
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_session_issuer') then
    -- The placeholder is generated in-process and never selected, logged, or
    -- written into a dump. The real secret is set out of band, the same way
    -- carr_jobs is handled in db/schema.sql's preamble.
    placeholder := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    execute format('create role %I login password %L', 'carr_session_issuer', placeholder);
  elsif not (select rolcanlogin from pg_roles where rolname = 'carr_session_issuer') then
    placeholder := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    execute format('alter role %I login password %L', 'carr_session_issuer', placeholder);
  end if;
end $$;

grant carr_session_minter to carr_session_issuer;

-- THE SEPARATION RUNS BOTH WAYS, and this is the half that is easy to forget.
-- ONE LEAKED SECRET MUST NOT BE ABLE TO BOTH MINT AND FILE.
--
-- The obvious direction is that the writer must not reach the mint. The other
-- direction matters just as much: the issuer must not reach the writer. An
-- issuer that could also insert evidence would let a single leaked credential
-- mint a session and then bind rows to it, which is precisely the attack 0204
-- exists to prevent, merely performed with a different secret.
--
-- So the issuer mints and does nothing else. It is NOT given the writer bundle,
-- and a leaked issuer credential can therefore create a session and then has
-- nothing to bind to it: 0204's guard requires the row's actor and tenant to
-- match, and the issuer cannot insert the row at all.
revoke all on schema public from carr_session_issuer;

-- --------------------------------------------------------------- apply-time
-- Catalog assertions only, and that is deliberate rather than lazy: 0204's
-- proof block exercises BEHAVIOUR because behaviour was what its mutants broke.
-- What this migration changes is a MEMBERSHIP GRAPH, and a membership graph is
-- read from the catalog — pg_has_role answers it transitively, which is the
-- part a hand-written "is X in Y" query gets wrong. Role-ACTING proofs (the
-- issuer can really mint; the issuer really cannot write evidence) need SET
-- ROLE and live in mcp-server/test/db/application_session_contract.py, which
-- runs as the roles rather than describing them.
do $$
declare
  members text;
begin
  if not exists (select 1 from pg_roles where rolname='carr_session_issuer' and rolcanlogin) then
    raise exception '0206 FAILED: carr_session_issuer must exist and be able to log in; '
                    'it is a connection credential, not a privilege bundle';
  end if;

  -- pg_has_role, not a join against pg_auth_members: membership is transitive,
  -- and a direct-edge query reports "not a member" for a role that reaches the
  -- mint through one hop. That is precisely the escalation worth catching.
  if not pg_has_role('carr_session_issuer', 'carr_session_minter', 'MEMBER') then
    raise exception '0206 FAILED: carr_session_issuer is not a member of '
                    'carr_session_minter, so nothing can mint and the substrate '
                    'is still inert';
  end if;

  if pg_has_role('carr_writer', 'carr_session_minter', 'MEMBER') then
    raise exception '0206 FAILED: carr_writer can reach carr_session_minter. The '
                    'routine write credential must never be able to manufacture '
                    'an authenticated session';
  end if;
  if pg_has_role('carr_writer', 'carr_session_issuer', 'MEMBER') then
    raise exception '0206 FAILED: carr_writer can assume carr_session_issuer, '
                    'which reaches the mint by one further hop';
  end if;

  -- The separation runs BOTH ways. An issuer that could also write evidence
  -- would let one leaked credential mint a session and bind rows to it.
  if pg_has_role('carr_session_issuer', 'carr_writer', 'MEMBER')
     or pg_has_role('carr_session_issuer', 'carr_authority', 'MEMBER') then
    raise exception '0206 FAILED: carr_session_issuer can assume a write or '
                    'authority credential; one leaked secret would then both '
                    'mint a session and file evidence against it';
  end if;

  -- Exactly one member. A second one is not automatically wrong, but it is a
  -- decision someone must make deliberately, and an unnoticed extra member is
  -- how a separation quietly stops separating.
  select string_agg(m.rolname, ', ' order by m.rolname) into members
    from pg_auth_members am
    join pg_roles r on r.oid = am.roleid
    join pg_roles m on m.oid = am.member
   where r.rolname = 'carr_session_minter';
  if members is distinct from 'carr_session_issuer' then
    raise exception '0206 FAILED: carr_session_minter members must be exactly '
                    'carr_session_issuer; found: %', coalesce(members, '(none)');
  end if;
end $$;
