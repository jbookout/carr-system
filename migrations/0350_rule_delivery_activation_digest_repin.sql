-- 0350_rule_delivery_activation_digest_repin.sql
--
-- WR-000019 changes after migration 0348 repinned the reviewed activation
-- overlay without changing the nine target rules. Refresh only those immutable
-- targets so the guarded cutover compares against the current reviewed map.

begin;

do $$
declare
  v_expected constant text := 'b513180786cf7212877870ab3bc14c03bb78b17b3397eb6ee474187a152b13f2';
  v_ids constant text[] := array[
    '25fcddee','3fa17fa0','72e06bdf','581cb3fe','113b3833',
    '57d13061','c66dc739','49533583','557838a5'
  ];
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0350 REFUSED: activation digest repin requires shadow mode';
  end if;

  if (select count(*) from ops.rule_delivery_activation_target
       where short_id = any(v_ids)) <> 9 then
    raise exception '0350 REFUSED: expected all nine reviewed activation targets';
  end if;

  update ops.rule_delivery_activation_target
     set map_digest = v_expected
   where short_id = any(v_ids)
     and map_digest <> v_expected;

  if exists (
    select 1
      from ops.rule_delivery_activation_target
     where short_id = any(v_ids)
       and map_digest <> v_expected
  ) then
    raise exception '0350 FAILED: a reviewed activation target still carries a stale digest';
  end if;

  if exists (
    select 1
      from ops.rule_delivery_activation_target
     where short_id <> all(v_ids)
  ) then
    raise exception '0350 REFUSED: unexpected activation targets exist outside the reviewed nine';
  end if;
end $$;

commit;
