-- provision-dell-local-client.sql — Dell's machine door, 2026-08-21, idea #78 item 2.
--
-- WHAT THIS IS. The sibling of provision-local-client.sql, for the OTHER Mac.
-- It inserts the one actor row a LOCAL_TOKENS bearer keyed 'dell-local'
-- authenticates as. mcp-server/src/mcp.js's callTool() looks up every write
-- actor by slug inside the write transaction and refuses actor_not_provisioned
-- if the row is missing, so without this row Dell's token authenticates fine
-- and then fails on that gate for every write.
--
-- WHY A SEPARATE SLUG AND NOT A SECOND KEY ON 'joe-local'. identity.js's
-- LOCAL_SPONSOR maps the machine slug to the sponsoring human, and the sponsor
-- is what decides whose personal brain a run carries. One shared machine
-- credential would make Dell's unattended runs write as Joe. The code half of
-- this was already landed 2026-08-18 — 'dell-local' has both a DISPLAY entry
-- and a LOCAL_SPONSOR entry in mcp-server/src/identity.js. Only the actor row
-- and the credential were ever missing.
--
-- THIS IS STEP 3 OF THE SAME FOUR-STEP RUNBOOK, and the three steps around it
-- are Joe's and Dell's hands, never a session's, because they touch a secret
-- value:
--   1. generate the token (openssl rand -hex 32)
--   2. wrangler secret put LOCAL_TOKENS — the FULL map, both keys:
--      {"joe-local":"<existing>","dell-local":"<new>"}
--      Cloudflare secrets are write-only and this one PUT replaces the whole
--      value, so joe-local's existing token must be carried into the same JSON
--      or Joe's own local door dies the moment Dell's is opened.
--   3. run THIS FILE — inserts the actor row below
--   4. add CARR_MCP_LOCAL_TOKEN=<token> to Dell's ~/.config/carr/mcp-tokens.env
--      (600, outside the repo, house convention)
-- This file touches no secret — it names an actor, not a credential — which is
-- why it is the one step safe to prepare ahead of the others.
--
-- IDEMPOTENT BY CONSTRUCTION. actor.slug is UNIQUE, so the insert guards on
-- conflict; a second run inserts nothing and changes nothing. An existing row
-- with the right slug but the wrong identity fields is refused, never repaired
-- silently by this provisioner.
--
-- HOW TO RUN IT (Joe's tap):
--   cd ~/carr-system
--   CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "provision dell-local actor (idea #78 item 2)" sql pipelines/provision-dell-local-client.sql
--
-- SUCCESS, EXACTLY. tools/db-tap.py executes SQL through psycopg, so it does
-- not print psql command tags or server notices. Require BOTH:
--   1. process exit status 0
--   2. this one pipe-separated stdout row, from the final SELECT:
--      dell-local|automation|Dell (local)|True
-- A second run has the same exit status and the same one-row stdout because the
-- insert is idempotent. ANYTHING ELSE MEANS STOP.
--
-- Because this is production break-glass, stderr also carries the
-- "BREAK-GLASS ENGAGED" banner, and db-tap appends the attempt to
-- out/break-glass-receipts.log before the SQL runs. Those two signals prove the
-- guarded attempt happened; the exit status and exact final row prove success.
--
-- TO RETIRE IT LATER (rotate the token first):
--   update actor set active = false where slug = 'dell-local';
-- Never delete the row outright — tool_call and tool_read_call rows carry a
-- foreign key to actor.id.

begin;

insert into actor (slug, kind, display_name)
values ('dell-local', 'automation', 'Dell (local)')
on conflict (slug) do nothing;

do $$
declare n int;
begin
  select count(*) into n from actor
   where slug = 'dell-local'
     and kind = 'automation'
     and display_name = 'Dell (local)'
     and active = true;
  if n <> 1 then
    raise exception 'expected one active dell-local automation actor named Dell (local); exact matches found: %', n;
  end if;
  raise notice 'dell-local actor provisioned (or already existed)';
end $$;

commit;

select slug, kind, display_name, active from actor where slug = 'dell-local';
