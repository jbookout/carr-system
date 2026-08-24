-- 0289_bind_delegation_model_effort_rule.sql
--
-- The binding half of one rule's enforcement, same shape as 0248: the control
-- is (a) registered in ops.enforcement_control_catalog (migration 0288, and
-- the reviewed map since PR #557), and approval further requires it be
-- (b) bound to the rule via ops.rule_control_binding with the sha256 of the
-- rule's current statement. Without this row ops.approve_rule refuses with
-- "exact enforcement is not installed", which is how it refused twice on
-- 2026-08-24 while the catalog row was already live.
--
-- The rule: a delegation names its SPECIFIC model and reasoning-effort level,
-- chosen as the cheapest model still capable of the task. Taught by Joe
-- 2026-08-24 ("select the specific model and effort level when you delegate a
-- task... cheapest qualified model... ensure the model working on the task is
-- capable") and approved by him the same session ("i approve it"). The deny
-- gate merged in PR #556 and its refusal was exercised live before admission.
--
-- The hash is computed from the live rule row, never hardcoded, so a reworded
-- statement makes this binding stale in exactly the way the approval join is
-- designed to notice.

begin;

insert into ops.rule_control_binding (rule_id, control_key, statement_hash, binding_contract)
select r.id, 'delegation_names_model_and_effort',
       encode(digest(r.statement,'sha256'),'hex'),
       jsonb_build_object(
         'source','migration 0289',
         'rule_id', r.id,
         'binding_moment','at dispatch of a delegated task to another seat, before anything reaches the desk',
         'human_order','Joe 2026-08-24: i approve it — the model-and-effort delegation rule taught the same session')
  from rule r
 where r.id = '6cfb67f5-6e85-48ad-9008-b0a82e2b71cc'
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
     where c.control_key = 'delegation_names_model_and_effort'
       and c.installed and c.verified_at is not null
       and c.enforcement_class = 'deny_gate'
       and b.rule_id = '6cfb67f5-6e85-48ad-9008-b0a82e2b71cc'
       and b.statement_hash = encode(digest(r.statement,'sha256'),'hex')
  ) then
    raise exception '0289 FAILED: delegation rule is not bound to its installed control';
  end if;
end $$;

commit;
