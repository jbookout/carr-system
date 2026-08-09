-- 0074_outside_model_actors.sql — actor rows for outside AI tools, loop #227.
--
-- WHAT AND WHY. Joe registered this Worker's /mcp endpoint in the Codex CLI
-- (OpenAI) and Grok Build CLI (xAI) via their native MCP configs. Both
-- authenticate through the SAME Google OAuth broker every human connector
-- uses (google-oidc.js) — there is no separate machine credential for
-- either tool, so every write they make was landing under actor 'joe',
-- indistinguishable from Joe using Claude Code directly. This migration adds
-- the two actor rows the Worker-side change (mcp-server/src/google-oidc.js,
-- mcp-server/src/identity.js, same loop) can attribute writes to once it
-- recognizes the OAuth client holding the grant.
--
-- IDENTITY-DERIVATION FINDING (full writeup in the loop #227 handoff).
-- google-oidc.js's /callback leg already receives the OAuth PROVIDER's
-- client_id for the app holding the grant (pending.req.clientId — this is
-- OUR workers-oauth-provider's client registry, unrelated to
-- env.GOOGLE_CLIENT_ID, which identifies this Worker to Google and is the
-- same for every human). That client_id resolves via
-- env.OAUTH_PROVIDER.lookupClient() to a ClientInfo carrying a self-declared
-- clientName from the app's OAuth dynamic client registration (RFC 7591).
-- Codex CLI is DCR-only (no pre-registered client id support as of 2026-08,
-- openai/codex#19154) and sends the literal client_name "Codex" — third-
-- party corroborated (a Figma MCP server allowlists DCR by exact
-- client_name and names "Codex" as one of two strings that pass, the other
-- being "Claude Code") but NOT YET observed directly against this Worker.
-- Grok Build CLI's exact client_name was NOT found in public sources as of
-- this loop, so identity.js's AGENT_CLIENT_NAMES map ships with only
-- 'codex' active; 'grok' is wired but commented out until confirmed live —
-- adding it is a one-line change once observed, never a guess landed as if
-- verified. Both are exact-match, case/space-insensitive, same discipline
-- as slugForEmail: a self-declared name is an ATTRIBUTION signal, checked
-- only after Google's identity check already passed, never a substitute for
-- it. See identity.js's AGENT_CLIENT_NAMES comment for the confirm-on-first-
-- connect method.
--
-- WHY 'automation' AND NOT A NEW kind, AND NOT human:true. actor.kind has a
-- three-way check constraint — human / automation / system
-- (migrations/0001_init.sql) — unchanged since 0001. codex/grok are
-- provisioned 'automation', same bucket as 'smoke-probe'
-- (pipelines/provision-smoke-probe.sql) and codex-reviewer/grok-reviewer
-- (pipelines/provision-review-council.sql), and the OAuth grant sets
-- props.human = false for them (google-oidc.js), which keeps every
-- humanOnly verb (teach, retire-rule, confirm-merge, reassign-deal — see
-- mcp.js's `tool.humanOnly && !actor.human` gate) Joe/Dell-only even on a
-- session where Joe is the one driving the CLI. This is a narrower, more
-- conservative default than 'joe' had: the point of this migration is
-- accountability for which TOOL wrote something, not widening what an
-- outside model may do unattended.
--
-- NOT THE SAME LANE AS codex-reviewer/grok-reviewer. Those two (0206-08-06,
-- pipelines/provision-review-council.sql) are the Automatic Review Council's
-- autonomous background reviewers, authenticated by a REVIEW_TOKENS bearer
-- that never touches Google OAuth and is locked server-side to the
-- 'reviewer' capability profile (exactly one write verb, record-finding).
-- 'codex' and 'grok' here are Joe's own interactive CLI sessions,
-- authenticated the same OAuth way Claude Code is, and — unless Joe scopes
-- their MCP config with ?profile= — get the same write surface a human
-- session gets, minus humanOnly verbs. Four actor rows, two different
-- trust models; do not merge or rename either pair into the other.
--
-- IDEMPOTENT BY CONSTRUCTION. actor.slug is UNIQUE (0001_init.sql); both
-- inserts guard on conflict, so a second run changes nothing.

begin;

insert into actor (slug, kind, display_name, active)
values ('codex', 'automation', 'Codex CLI (outside-model agent surface, loop #227)', true)
on conflict (slug) do nothing;

insert into actor (slug, kind, display_name, active)
values ('grok', 'automation', 'Grok Build CLI (outside-model agent surface, loop #227)', true)
on conflict (slug) do nothing;

commit;

do $$
declare n_codex int;
declare n_grok int;
begin
  select count(*) into n_codex from actor where slug = 'codex';
  select count(*) into n_grok  from actor where slug = 'grok';
  if n_codex <> 1 then
    raise exception '0074: expected exactly 1 actor row for slug codex, found %', n_codex;
  end if;
  if n_grok <> 1 then
    raise exception '0074: expected exactly 1 actor row for slug grok, found %', n_grok;
  end if;
  raise notice '0074: codex actor provisioned (or already existed)';
  raise notice '0074: grok actor provisioned (or already existed)';
end $$;

select slug, kind, display_name, active from actor
where slug in ('codex', 'grok')
order by slug;
