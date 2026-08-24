-- 0288_control_catalog_delegation_model_effort.sql
--
-- One control for one taught rule: a delegation names its specific model and
-- reasoning effort, chosen as the cheapest model still capable of the task
-- (Joe, 2026-08-24, approved the same day). The deny gate merged in PR #556 —
-- tools/room-bridge/dispatch.py refuses a codex-kind desk lacking either —
-- and PR #557 declares it in the reviewed map with a CI-run selftest.
--
-- WHY A MIGRATION AND NOT ONLY THE SYNC. A freshly seeded database (CI's, or
-- any rebuild) holds only what migrations insert; the parity gate then compares
-- it against the map and fails on the difference — correctly, but for a reason
-- nobody could act on. The sync converges Production; this row makes every
-- seed carry the same catalog. Same split as 0274/0275.
--
-- Insert-only with on conflict do nothing: if the sync reached the database
-- first, its row (same map-compiled content) stands.

begin;

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('delegation_names_model_and_effort',
        'tools/room-bridge/dispatch.py; tools/room-bridge/desks.py',
        'ops/room-bridge-selftest.py',
        'deny_gate', true, now())
on conflict (control_key) do nothing;

do $$
declare n int;
begin
  select count(*) into n from ops.enforcement_control_catalog
   where control_key = 'delegation_names_model_and_effort';
  if n <> 1 then
    raise exception 'delegation_names_model_and_effort control missing after seed';
  end if;
end $$;

commit;
