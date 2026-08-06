-- provision-review-council.sql — Automatic Review Council, 2026-08-06.
--
-- WHAT THIS IS. Inserts the actor rows a REVIEW_TOKENS bearer authenticates
-- as. mcp-server/src/mcp.js's callTool() looks up every write actor by slug
-- inside the write transaction (`select id from actor where slug=$1`) and
-- refuses actor_not_provisioned if the row is missing — exactly the same gate
-- joe/dell/automation/system/smoke-probe hit, with no special case for a
-- reviewer token. Without a reviewer's actor row, the one write verb the
-- 'reviewer' profile allows (record-finding) would authenticate fine and
-- then fail on that gate, even though it is the only write that profile ever
-- attempts.
--
-- TWO ROWS, BOTH ACTIVE — scope history below, because it changed mid-build:
--   codex-reviewer  active=true  — Codex-only automation, subscription-covered
--                                  via ChatGPT-token auth (~/.codex/auth.json).
--                                  The ORIGINAL frozen scope this file was
--                                  first written under.
--   grok-reviewer   active=true  — SCOPE EXTENSION, same day. Originally this
--                                  row was provisioned active=false ("Grok
--                                  stays manual per Joe's 2026-08-06 cost
--                                  override, decision 65468572"). Before this
--                                  file was ever run against production, the
--                                  coordinator reported the override lifted:
--                                  Grok Build CLI 0.2.118 installed, `grok
--                                  login` (OAuth device flow) completed, and
--                                  the subscription-covered headless path
--                                  live-verified — confirmed independently
--                                  from this machine (see
--                                  pipelines/run_codex_review.py's module
--                                  docstring for the exact live checks: no
--                                  XAI_API_KEY in the environment, a read-only
--                                  prompt completing cleanly with no approval
--                                  flag, and — the load-bearing one — a write
--                                  attempt inside a real git worktree
--                                  kernel-blocked by `--sandbox read-only`,
--                                  logged as an FsViolation in
--                                  ~/.grok/sandbox-events.jsonl). So this row
--                                  ships active=true directly; there is no
--                                  "flip it later" step because the flip
--                                  already happened, in code, before this
--                                  file was ever executed. If decision
--                                  65468572 is reinstated, the retirement
--                                  path at the bottom of this file applies to
--                                  grok-reviewer exactly as it would to
--                                  codex-reviewer.
--
-- THIS IS ONE STEP OF SEVERAL. The full provisioning runbook (JOE ONLY for
-- the steps that touch a secret) is written at the top of
-- bin/review-council-runner.sh:
--   1. generate BOTH tokens (openssl rand -hex 32, twice)
--   2. wrangler secret put REVIEW_TOKENS — a TWO-KEY JSON map:
--        {"codex-reviewer":"<token 1>","grok-reviewer":"<token 2>"}
--   3. run THIS FILE                        — inserts both actor rows below
--   4. add BOTH to ~/.config/carr/mcp-tokens.env:
--        CARR_MCP_REVIEW_TOKEN_CODEX=<token 1>
--        CARR_MCP_REVIEW_TOKEN_GROK=<token 2>
--   5. deploy the Worker (classifier-gated; Joe/the parent session runs this)
--   6. optional: npm i -g @openai/codex, if the runner reports INSTALL NEEDED
--      for Codex (Grok is already installed and logged in — see above)
--   7. launchd registration (list-before-create; the parent session's step,
--      deliberately NOT part of this build)
-- Steps 1, 2 and 4 touch a secret value and are JOE ONLY. This file touches
-- no secret — it names two actors, not a credential — so it is the one step
-- an agent can prepare. It has NOT been run as part of preparing it.
--
-- WHY 'automation' AND NOT A NEW kind. actor.kind has a three-way check
-- constraint — human / automation / system (migrations/0001_init.sql) — and a
-- review token is exactly what 'automation' already means: a non-human caller
-- acting under its own identity, same bucket as 'smoke-probe'
-- (pipelines/provision-smoke-probe.sql) and the 'automation' row seeded for
-- scheduled jobs in migrations/0002_seed.sql. Widening the constraint for two
-- more rows would be a schema change to avoid reusing a category that already
-- fits.
--
-- IDEMPOTENT BY CONSTRUCTION. actor.slug is UNIQUE (migrations/0001_init.sql),
-- so both inserts guard on conflict; a second run inserts nothing and changes
-- nothing. Because neither row has ever been run against production, there is
-- no "already inactive, needs a flip" state to migrate — both ship active
-- from the first real run. (If a future scope change needs to deactivate one,
-- that is a plain `update actor set active = false where slug = ...`, run
-- directly — this file's ON CONFLICT DO NOTHING deliberately never re-touches
-- an existing row, so it can never be the thing that silently re-locks or
-- re-opens a lane a human decided on separately.)
--
-- HOW TO RUN IT (Joe's tap; an agent has no writer credential). Preferred
-- path is tools/db-tap.py (2026-07-31) — it obtains the connection string
-- inside the process rather than through shell command substitution, so the
-- DSN never touches a command string or shell history:
--   cd ~/carr-system
--   .venv/bin/python tools/db-tap.py sql pipelines/provision-review-council.sql
--
-- Rehearse on a branch first if there is any doubt:
--   .venv/bin/python tools/db-tap.py --branch <rehearsal-branch> sql pipelines/provision-review-council.sql
--
-- EXPECTED, EXACTLY:
--   BEGIN
--   INSERT 0 1
--   INSERT 0 1
--   NOTICE:  codex-reviewer actor provisioned (or already existed)
--   NOTICE:  grok-reviewer actor provisioned (or already existed)
--   DO
--   COMMIT
-- then a two-row table: codex-reviewer (active=true), grok-reviewer
-- (active=true). A SECOND run prints INSERT 0 0 twice and the identical
-- notices and table. ANYTHING ELSE MEANS STOP.
--
-- TO RETIRE EITHER LATER (rotate its token first, per secrets-inventory.md's
-- rule that a credential value is never left live after its consumer is
-- gone):
--   update actor set active = false where slug = 'codex-reviewer';
--   update actor set active = false where slug = 'grok-reviewer';
-- Never delete either row outright — tool_call and event rows a reviewer
-- actor wrote carry a foreign key to actor.id, and deleting the actor would
-- either cascade-destroy that history or fail on the FK, neither of which is
-- the right response to "we don't use this lane any more".
--
-- NOTE ON active AND THE WRITE GATE. mcp.js's callTool() write-transaction
-- lookup is `select id from actor where slug=$1` — it does not filter on
-- active today, for ANY actor (joe/dell/smoke-probe included). The REAL
-- control on which reviewer can actually write is which slugs exist in the
-- REVIEW_TOKENS secret map (step 2 above) — active=true/false here is
-- documentation-as-data, visible to anyone reading the actor table, not a
-- second independent gate. Do not treat provisioning a row as authorization
-- to mint its token; token minting is a separate, explicit, JOE ONLY step.

begin;

insert into actor (slug, kind, display_name, active)
values ('codex-reviewer', 'automation', 'Codex Reviewer (Automatic Review Council)', true)
on conflict (slug) do nothing;

insert into actor (slug, kind, display_name, active)
values ('grok-reviewer', 'automation', 'Grok Reviewer (Automatic Review Council)', true)
on conflict (slug) do nothing;

do $$
declare n_codex int;
declare n_grok int;
begin
  select count(*) into n_codex from actor where slug = 'codex-reviewer';
  select count(*) into n_grok from actor where slug = 'grok-reviewer';
  if n_codex <> 1 then
    raise exception 'expected exactly 1 actor row for slug codex-reviewer, found %', n_codex;
  end if;
  if n_grok <> 1 then
    raise exception 'expected exactly 1 actor row for slug grok-reviewer, found %', n_grok;
  end if;
  raise notice 'codex-reviewer actor provisioned (or already existed)';
  raise notice 'grok-reviewer actor provisioned (or already existed)';
end $$;

commit;

select slug, kind, display_name, active from actor
where slug in ('codex-reviewer', 'grok-reviewer')
order by slug;
