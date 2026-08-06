-- provision-smoke-probe.sql — loop #192, 2026-08-06 smoke-suite re-credit.
--
-- WHAT THIS IS. Inserts the ONE actor row a PROBE_TOKENS bearer authenticates
-- as: 'smoke-probe', kind='automation'. mcp-server/src/mcp.js's callTool()
-- looks up every write actor by slug inside the write transaction
-- (`select id from actor where slug=$1`) and refuses actor_not_provisioned if
-- the row is missing — exactly the same gate joe/dell/automation/system hit,
-- with no special case for the probe token. Without this row, the three write
-- verbs the smoke-probe profile allows (log-activity, set-next-action,
-- complete-action) authenticate fine and then fail on that gate, even though
-- every one of them is only ever going to REPLAY a frozen fixture and never
-- insert a new row (see the 'probe' entry in mcp-server/src/mcp.js's PROFILES
-- for why that replay is safe under a different actor id than the one that
-- originally wrote the fixture).
--
-- THIS IS ONE STEP OF FOUR. The full provisioning runbook (JOE ONLY for the
-- steps that touch a secret or a production credential) is written at the top
-- of mcp-server/smoke-reads.sh, just above its PREFLIGHT block:
--   1. generate the token (openssl rand -hex 32)
--   2. wrangler secret put PROBE_TOKENS   — {"smoke-probe":"<token>"}
--   3. run THIS FILE                       — inserts the actor row below
--   4. add CARR_MCP_PROBE_TOKEN=<token> to ~/.config/carr/mcp-tokens.env
-- Steps 1, 2 and 4 touch a secret value and are JOE ONLY. This file touches
-- no secret — it names an actor, not a credential — so it is the one step an
-- agent can prepare. It has NOT been run as part of preparing it.
--
-- WHY 'automation' AND NOT A NEW kind. actor.kind has a three-way check
-- constraint — human / automation / system (migrations/0001_init.sql) — and a
-- probe token is exactly what 'automation' already means: a non-human caller
-- acting under its own identity, same bucket as the 'automation' row seeded
-- for scheduled jobs in migrations/0002_seed.sql. Widening the constraint for
-- one more row would be a schema change to avoid reusing a category that
-- already fits.
--
-- IDEMPOTENT BY CONSTRUCTION. actor.slug is UNIQUE (migrations/0001_init.sql),
-- so the insert guards on conflict; a second run inserts nothing and changes
-- nothing.
--
-- HOW TO RUN IT (Joe's tap; an agent has no writer credential). Preferred path
-- is tools/db-tap.py (2026-07-31) — it obtains the connection string inside the
-- process rather than through shell command substitution, so the DSN never
-- touches a command string or shell history:
--   cd ~/carr-system
--   .venv/bin/python tools/db-tap.py sql pipelines/provision-smoke-probe.sql
--
-- Rehearse on a branch first if there is any doubt:
--   .venv/bin/python tools/db-tap.py --branch <rehearsal-branch> sql pipelines/provision-smoke-probe.sql
--
-- Equivalent, older path (same pattern as e.g. pipelines/r2-quota-seed.sql, from
-- before db-tap existed) if db-tap is ever unavailable:
--   export PATH="/opt/homebrew/opt/libpq/bin:/opt/homebrew/bin:$PATH"
--   cd ~/carr-system
--   export DATABASE_URL="$(cd mcp-server && npx -y neonctl connection-string production \
--     --project-id steep-field-48688294 --org-id org-dry-dew-75906281 \
--     --role-name neondb_owner --database-name neondb)"
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f pipelines/provision-smoke-probe.sql
--
-- EXPECTED, EXACTLY:
--   BEGIN
--   INSERT 0 1
--   NOTICE:  smoke-probe actor provisioned (or already existed)
--   DO
--   COMMIT
-- then a one-row table: slug='smoke-probe', kind='automation',
-- display_name='Smoke Probe', active=true. A SECOND run prints INSERT 0 0 and
-- the identical NOTICE and table. ANYTHING ELSE MEANS STOP.
--
-- TO RETIRE IT LATER (rotate the token first, per secrets-inventory.md's rule
-- that a credential value is never left live after its consumer is gone):
--   update actor set active = false where slug = 'smoke-probe';
-- Never delete the row outright — tool_call and event rows the probe actor
-- wrote (the replayed fixtures) carry a foreign key to actor.id, and deleting
-- the actor would either cascade-destroy that history or fail on the FK,
-- neither of which is the right response to "we don't use this token anymore".

begin;

insert into actor (slug, kind, display_name)
values ('smoke-probe', 'automation', 'Smoke Probe')
on conflict (slug) do nothing;

do $$
declare n int;
begin
  select count(*) into n from actor where slug = 'smoke-probe';
  if n <> 1 then
    raise exception 'expected exactly 1 actor row for slug smoke-probe, found %', n;
  end if;
  raise notice 'smoke-probe actor provisioned (or already existed)';
end $$;

commit;

select slug, kind, display_name, active from actor where slug = 'smoke-probe';
