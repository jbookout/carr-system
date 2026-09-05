-- 0481: repin the eight rule-delivery activation targets onto the reviewed map
-- that carries rules 737a68d6 and a7784a18.
--
-- The two additions are Layer 0 rules and do not change any of the eight
-- reviewed pack-cutover targets. They do change the byte identity of the base
-- map. The activation preimage is deliberately digest-bound, so move those
-- unchanged targets forward with a guarded migration instead of rewriting the
-- sealed 0478 history.

do $rule_map_repin_after_rule_activation_repair$
declare updated integer; preimage integer;
begin
  select count(*) into preimage
    from ops.rule_delivery_activation_target
   where map_digest='eebfa2d627dfbbc65ae06e623724487158b940c9376cd30dbb067aec2779e8bb';
  if preimage<>8 then
    raise exception '0481 REFUSED: expected eight activation targets on the post-0478 map digest, found %',preimage;
  end if;

  update ops.rule_delivery_activation_target
     set map_digest='f76a976fc39e19aeb6c4e962e0ac36510fb9ca68f3b73ba01acd60d27f571816'
   where map_digest='eebfa2d627dfbbc65ae06e623724487158b940c9376cd30dbb067aec2779e8bb';
  get diagnostics updated=row_count;
  if updated<>8 then
    raise exception '0481 REFUSED: expected eight exact rule-delivery target repins, changed %',updated;
  end if;
end $rule_map_repin_after_rule_activation_repair$;
