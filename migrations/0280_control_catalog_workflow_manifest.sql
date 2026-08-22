-- 0280_control_catalog_workflow_manifest.sql
--
-- The second control the enforcement declarations did not carry.
--
-- ops/control-catalog-parity-gate.py found it the same way it found ci_gates,
-- which 0275 seeded: a control live in Production's catalog and declared in no
-- inventory. This one is control-plane-workflow-manifest, seeded into
-- ops.rule_enforcement_point by 0148 alongside the rule it backs — the active
-- rule forbidding a cognition token to be spent on state, recurrence, routing,
-- validation, or any decision already expressible as a tested predicate. That
-- rule is the code-first design test in force; this control is how it is
-- enforced rather than recited.
--
-- WITHOUT THIS, a database rebuilt from the repository holds 62 controls while
-- Production holds 63, and the parity gate fails on the difference — correctly,
-- and for a reason a rebuild cannot act on, because the row it is missing was
-- never declared anywhere it could be seeded from.
--
-- 0148 is long applied and its sha256 is checked, so the row is added here
-- rather than folded back into it.
--
-- NOTHING NEEDED RECONCILING, unlike ci_gates. That row carried prose naming a
-- class inside a file, so its reference could not resolve. This one already
-- carries two real tracked paths in Production, so the values below are the
-- live row exactly rather than a corrected version of it. Verified live on
-- 2026-08-22 before writing: implementation_ref
-- ops/config/control-plane-workflows.v1.json, test_ref
-- ops/control-plane-selftest.py, enforcement_class deny_gate, installed true.
--
-- Insert-only, and on conflict do nothing, for the reason 0274 learned the hard
-- way: a blanket update rewrites rows the active_approved_control_immutable
-- trigger protects, and Production refuses the whole migration rather than the
-- one row. Seeding a missing row must never be able to touch a present one.

begin;

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('control-plane-workflow-manifest',
        'ops/config/control-plane-workflows.v1.json',
        'ops/control-plane-selftest.py',
        'deny_gate', true, now())
on conflict (control_key) do nothing;

do $$
declare n int;
begin
  select count(*) into n from ops.enforcement_control_catalog
   where control_key = 'control-plane-workflow-manifest';
  if n <> 1 then
    raise exception 'control-plane-workflow-manifest control missing after seed';
  end if;
end $$;

commit;
