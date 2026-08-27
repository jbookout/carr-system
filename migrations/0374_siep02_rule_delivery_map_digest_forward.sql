-- 0374 / SIEP-02 post-rebase integration: advance the scoped-rule activation preimage
-- to the exact current reviewed enforcement map without changing delivery mode.
-- Source/test implementation only. Production application or enforcement still
-- requires Joe's explicit approval through the existing guarded rollout.

do $$
declare
  v_old constant text := '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218';
  v_new constant text := '0bd85004ed17b2da8aa42cbff0e4b3546fafee0c28df2969e2cebbf412fcc7ec';
  v_updated bigint;
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0350 REFUSED: rule-delivery map refresh requires shadow mode';
  end if;
  if (select count(*) from ops.rule_delivery_activation_target) <> 9 then
    raise exception '0350 REFUSED: activation target set is not exactly nine';
  end if;
  if exists (
    select 1 from ops.rule_delivery_activation_target
     where map_digest not in (v_old,v_new)
  ) then
    raise exception '0350 REFUSED: activation target carries an unknown map digest';
  end if;

  update ops.rule_delivery_activation_target
     set map_digest=v_new
   where map_digest=v_old;
  get diagnostics v_updated=row_count;
  if v_updated not in (0,9) then
    raise exception '0350 REFUSED: partial activation target refresh changed % rows',v_updated;
  end if;
  if (select count(*) from ops.rule_delivery_activation_target where map_digest=v_new) <> 9 then
    raise exception '0350 FAILED: activation targets do not bind the exact current map';
  end if;
end $$;
