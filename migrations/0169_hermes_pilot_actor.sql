-- 0169_hermes_pilot_actor.sql — the actor row every Hermes write needs.
--
-- WHAT HAPPENED. On 2026-08-16 the Hermes runtime was granted nine additive
-- write verbs, the profile was proven by 401 passing tests, the door was
-- deployed, and the first real write failed:
--
--   {"error":"actor_not_provisioned","slug":"hermes-pilot",
--    "hint":"the token authenticates as this actor but no row exists in the
--            actor table — provision the actor before any write verb will run"}
--
-- Reads had been working for hours, because a read does not touch the actor
-- table. Every layer of the write path was built and tested except the row the
-- write transaction looks itself up in.
--
-- THIS IS THE SECOND TIME TODAY, IN THE SAME SHAPE. The release candidate this
-- morning was written without its evidence refs and the constraint was found by
-- Joe hitting it (defect 61d2f0f8). Rule 5409731b names the general case: a new
-- actor or trigger changes the permission surface of every table it touches, so
-- grant-check every table rather than the one being edited. Registering the
-- identity in SERVER_MACHINE_IDENTITIES and HERMES_SPONSOR is what makes the
-- token resolve; this row is what makes it able to write. Three registrations,
-- three files, and the third was invisible until a live write asked for it.
--
-- WHY 'automation' AND NOT A NEW kind. actor.kind carries a three-way check
-- constraint — human / automation / system — unchanged since 0001. This is the
-- same bucket as smoke-probe, codex-reviewer, grok-reviewer and the outside-
-- model CLI seats of 0074. A persistent runtime holding additive write verbs is
-- an automation by every definition the constraint offers, and inventing a
-- fourth kind for it would be a schema change dressed up as a label.
--
-- WHAT THIS ROW DOES NOT GRANT. Nothing. The verb surface is decided entirely
-- by the locked `hermes` profile in mcp-server/src/mcp.js: nine additive verbs,
-- every other write refusing with not_in_profile, no humanOnly verb reachable
-- on human:false, and no send verb existing anywhere in this Worker. This row
-- only lets a write that is already permitted find the actor it is attributed
-- to. Its absence was a missing registration, never a boundary doing its job.

begin;

insert into actor (slug, kind, display_name, active)
values ('hermes-pilot', 'automation',
        'Hermes Agent (R0 evaluation runtime, Joe-sponsored, additive write grant 2026-08-16)',
        true)
on conflict (slug) do nothing;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare n int;
declare k text;
begin
  select count(*), max(kind) into n, k from actor where slug = 'hermes-pilot';
  if n <> 1 then
    raise exception '0169 FAILED: expected exactly 1 hermes-pilot actor row, found %', n;
  end if;
  if k <> 'automation' then
    raise exception '0169 FAILED: hermes-pilot must be kind automation, found %', k;
  end if;
  raise notice '0169 OK: hermes-pilot provisioned as an automation actor';
end $$;
