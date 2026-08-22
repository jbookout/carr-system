-- 0248_register_conduct_stop_control.sql
-- Register the conduct-stop gate in the enforcement-control catalog and bind
-- it to the autonomy rule (taught 2026-08-21, Joe's approval order on record:
-- "approve the autonomy rule so sessions stop asking").
--
-- WHY: ops.approve_rule refuses activation unless the requested control is
-- (a) a catalog row that is installed, verified, and gate-class, AND
-- (b) bound to the rule via ops.rule_control_binding with the sha256 of the
-- rule's CURRENT statement. The catalog held only the three controls seeded
-- by 0228; the conduct-stop gate lived only in the repo's enforcement map
-- (ops/config/rule-enforcement-map.json, control_catalog.conduct_stop), so
-- the approval's join came back empty and the refusal surfaced as a bare
-- internal error (defect class verb-refuses-its-own-required-path,
-- 2 occurrences 2026-08-22 — the masking is a separate fix).
--
-- The binding hashes the statement at apply time, so a rule edited after this
-- migration ships stops matching and approval refuses — which is the
-- preimage-pinning behavior 0247 established for the earlier system rules.

begin;

insert into ops.enforcement_control_catalog
  (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values
  ('conduct_stop',
   'hooks/conduct-stop-gate.py',
   'ops/conduct-gate-selftest.py; ops/delta-resend-selftest.py',
   'stop_gate', true, now())
on conflict (control_key) do nothing;

insert into ops.rule_control_binding (rule_id, control_key, statement_hash, binding_contract)
select r.id, 'conduct_stop', encode(digest(r.statement,'sha256'),'hex'),
       jsonb_build_object(
         'source','migration 0248',
         'rule_id', r.id,
         'binding_moment','turn end: a session about to park a delivery-mechanics step (commit, push, PR, merge, branch cleanup) on a partner',
         'human_order','Joe 2026-08-21: approve the autonomy rule so sessions stop asking')
  from rule r
 where r.id = '3fa422b7-7c99-49fc-8e22-1e551a975c6f'
   and r.status = 'proposed'
on conflict (rule_id, control_key) do nothing;

-- Verification: the approval path's own join must now see the control bound
-- to the rule with a matching current-statement hash.
do $$
begin
  if not exists (
    select 1
      from ops.enforcement_control_catalog c
      join ops.rule_control_binding b using (control_key)
      join rule r on r.id = b.rule_id
     where c.control_key = 'conduct_stop'
       and c.installed and c.verified_at is not null
       and c.enforcement_class = 'stop_gate'
       and b.rule_id = '3fa422b7-7c99-49fc-8e22-1e551a975c6f'
       and b.statement_hash = encode(digest(r.statement,'sha256'),'hex')
  ) then
    raise exception '0248 FAILED: conduct_stop is not visible to the approval join for the autonomy rule';
  end if;
end $$;

commit;
