-- 0292_pack_delivery_control_catalog.sql
--
-- pack_delivery is declared in the reviewed map (hooks/rule-pack-drift-gate.py)
-- and classified stop_gate in control-enforcement-classes.v1.json. A freshly
-- seeded database only holds what migrations insert; the parity gate then
-- compares the table to the map and fails on the hole — correctly, but with
-- no row anyone can act on. Same split as 0288: the sync converges
-- production; this insert makes every seed carry the catalog row.
--
-- Insert-only with on conflict do nothing: if the sync reached the database
-- first, its row stands.

begin;

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('pack_delivery',
        'hooks/rule-pack-drift-gate.py',
        'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py',
        'stop_gate', true, now())
on conflict (control_key) do nothing;

do $$
declare n int;
begin
  select count(*) into n from ops.enforcement_control_catalog
   where control_key = 'pack_delivery';
  if n <> 1 then
    raise exception 'pack_delivery control missing after seed';
  end if;
end $$;

commit;
