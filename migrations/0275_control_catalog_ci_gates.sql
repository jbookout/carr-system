-- 0275_control_catalog_ci_gates.sql
--
-- One control the enforcement declarations did not carry.
--
-- ops/control-catalog-parity-gate.py found it the way it was designed to: after
-- 0274 seeded Production, the catalog there held one control declared in no
-- inventory — ci_gates, live since before tonight. 0274 is already applied and
-- its sha256 is checked, so the row is added here rather than folded into it.
--
-- WITHOUT THIS, a freshly seeded database would hold 61 controls while the
-- repository declares 62, and the parity gate would fail on the difference —
-- correctly, but for a reason nobody could act on.
--
-- Insert-only, for the same reason 0274 is: Production's existing row carries
-- implementation_ref 'ops/ci.sh gates class', prose naming a class inside a
-- file rather than a path, and this declares the file itself so the reference
-- resolves and can be verified as tracked. Reconciling that difference on a live
-- row is a decision, not a seed, and is deliberately left to a person; the
-- companion declaration file records it beside the entry.

begin;

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('ci_gates', 'ops/ci.sh', 'ops/hermes-autonomy-check-selftest.py', 'surfacing', true, now())
on conflict (control_key) do nothing;

do $$
declare n int;
begin
  select count(*) into n from ops.enforcement_control_catalog where control_key = 'ci_gates';
  if n <> 1 then raise exception 'ci_gates control missing after seed'; end if;
end $$;

commit;
