-- 0378 / SIEP-02 post-main integration: advance the scoped-rule activation preimage
-- to the exact reviewed map from PR #732 without changing delivery mode.
-- Source/test implementation only. Production application or enforcement
-- remains subject to Joe's explicit go/no-go.

do $$
declare
  v_old constant text := '0bd85004ed17b2da8aa42cbff0e4b3546fafee0c28df2969e2cebbf412fcc7ec';
  v_new constant text := 'b513180786cf7212877870ab3bc14c03bb78b17b3397eb6ee474187a152b13f2';
  v_updated bigint;
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0354 REFUSED: rule-delivery map refresh requires shadow mode';
  end if;
  if (select count(*) from ops.rule_delivery_activation_target) <> 9 then
    raise exception '0354 REFUSED: activation target set is not exactly nine';
  end if;
  if exists (
    select 1 from ops.rule_delivery_activation_target
     where map_digest not in (v_old,v_new)
  ) then
    raise exception '0354 REFUSED: activation target carries an unknown map digest';
  end if;

  update ops.rule_delivery_activation_target
     set map_digest=v_new
   where map_digest=v_old;
  get diagnostics v_updated=row_count;
  if v_updated not in (0,9) then
    raise exception '0354 REFUSED: partial activation target refresh changed % rows',v_updated;
  end if;
  if (select count(*) from ops.rule_delivery_activation_target where map_digest=v_new) <> 9 then
    raise exception '0354 FAILED: activation targets do not bind the exact current map';
  end if;
end $$;
