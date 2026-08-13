-- provision-local-client.sql — Phase 1, 2026-08-13, decision 97e76a2f.
--
-- WHAT THIS IS. Inserts the ONE actor row a LOCAL_TOKENS bearer authenticates
-- as: 'joe-local', kind='automation'. mcp-server/src/mcp.js's callTool() looks
-- up every write actor by slug inside the write transaction
-- (`select id from actor where slug=$1`) and refuses actor_not_provisioned if
-- the row is missing — exactly the same gate joe/dell/codex/grok/smoke-probe
-- hit, with no special case for the local token. Without this row, every
-- write verb `run.sh call` sends through the deployed Worker would
-- authenticate fine and then fail on that gate.
--
-- WHY THIS EXISTS. `run.sh call <verb>` (tools/call-verb.py ->
-- mcp-server/local-verb.mjs) used to open its own direct connection to the
-- production database and run a verb's handler locally — bypassing this
-- Worker's identity derivation, the profile gate in mcp.js, and the
-- tool_read_call instrumentation entirely. local-verb.mjs is now a thin HTTPS
-- client of the deployed Worker's /mcp endpoint, carrying a LOCAL_TOKENS
-- bearer (see mcp-server/src/index.js's "local token" comment and
-- mcp-server/wrangler.toml's secrets header). This row is the actor that
-- bearer resolves to.
--
-- THIS IS ONE STEP OF A SMALL RUNBOOK, same shape as
-- pipelines/provision-smoke-probe.sql:
--   1. generate the token (openssl rand -hex 32)
--   2. wrangler secret put LOCAL_TOKENS   — {"joe-local":"<token>"}
--   3. run THIS FILE                       — inserts the actor row below
--   4. add CARR_MCP_LOCAL_TOKEN=<token> to ~/.config/carr/mcp-tokens.env (600,
--      outside the repo, house convention — see ingest.env, mcp-tokens.env)
-- Steps 1, 2 and 4 touch a secret value. This file touches no secret — it
-- names an actor, not a credential — so it is the one step that is safe to
-- prepare ahead of the others.
--
-- WHY 'automation' AND NOT A NEW kind. actor.kind has a three-way check
-- constraint — human / automation / system (migrations/0001_init.sql) — and a
-- machine credential is exactly what 'automation' already means, same bucket
-- as smoke-probe, codex-reviewer, grok-reviewer, and codex/grok themselves
-- (migrations/0074_outside_model_actors.sql). human:false is set server-side
-- in identity.js's agentActorForToken regardless of this row's kind; the row
-- only has to exist and be active.
--
-- IDEMPOTENT BY CONSTRUCTION. actor.slug is UNIQUE (migrations/0001_init.sql),
-- so the insert guards on conflict; a second run inserts nothing and changes
-- nothing.
--
-- HOW TO RUN IT (Joe's tap; an agent has no writer credential of its own —
-- but see tools/db-tap.py's break-glass envelope for the one exception, which
-- an agent may use with CARR_BREAK_GLASS=1 and a stated --reason). Preferred
-- path is tools/db-tap.py (2026-07-31) — it obtains the connection string
-- inside the process rather than through shell command substitution, so the
-- DSN never touches a command string or shell history:
--   cd ~/carr-system
--   CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "provision joe-local actor (decision 97e76a2f)" sql pipelines/provision-local-client.sql
--
-- Rehearse on a branch first if there is any doubt:
--   .venv/bin/python tools/db-tap.py --branch <rehearsal-branch> sql pipelines/provision-local-client.sql
--
-- EXPECTED, EXACTLY:
--   BEGIN
--   INSERT 0 1
--   NOTICE:  joe-local actor provisioned (or already existed)
--   DO
--   COMMIT
-- then a one-row table: slug='joe-local', kind='automation',
-- display_name='Joe (local)', active=true. A SECOND run prints INSERT 0 0 and
-- the identical NOTICE and table. ANYTHING ELSE MEANS STOP.
--
-- TO RETIRE IT LATER (rotate the token first, per secrets-inventory.md's rule
-- that a credential value is never left live after its consumer is gone):
--   update actor set active = false where slug = 'joe-local';
-- Never delete the row outright — tool_call and tool_read_call rows this actor
-- writes carry a foreign key to actor.id, and deleting the actor would either
-- cascade-destroy that history or fail on the FK, neither of which is the
-- right response to "we don't use this token anymore".

begin;

insert into actor (slug, kind, display_name)
values ('joe-local', 'automation', 'Joe (local)')
on conflict (slug) do nothing;

do $$
declare n int;
begin
  select count(*) into n from actor where slug = 'joe-local';
  if n <> 1 then
    raise exception 'expected exactly 1 actor row for slug joe-local, found %', n;
  end if;
  raise notice 'joe-local actor provisioned (or already existed)';
end $$;

commit;

select slug, kind, display_name, active from actor where slug = 'joe-local';
