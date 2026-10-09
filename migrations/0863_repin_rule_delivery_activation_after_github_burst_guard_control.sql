-- rollback: forward-only — a repin only moves the guarded map-digest preimage; a later repin migration moves it again
-- Registering the github-burst-guard control changes the reviewed map's byte
-- digest (ops/config/rule-enforcement-map.json gains one control_catalog entry;
-- no rule text, delivery mode, approval or rule-to-control binding moves). The
-- pack cutover contracts stay the same; move their identity forward without
-- changing anything else, after 0851 repinned them for the sizing rule.
do $rule_delivery_0863$
declare
  v_old constant text := '2b217aec8841409c9a9c4aa659dad74f81da4750795b03ec3f4f490bac4a76f4';
  v_new constant text := 'c7b9c8a4e2d4f7b3a6a161a1959f346501746d49d15c74a57272df307bc72842';
  v_ids constant text[] := array[
    '113b3833','25fcddee','3fa17fa0','49533583',
    '557838a5','57d13061','72e06bdf','c66dc739'
  ];
  v_actual_ids text[];
  v_count integer;
  v_old_count integer;
begin
  -- Cutover takes this same first token. The table lock also prevents an
  -- insertion or deletion between the preimage check and the update.
  perform 1 from ops.rule_delivery_policy where singleton for update;
  lock table ops.rule_delivery_activation_target in share row exclusive mode;
  select count(*), array_agg(short_id order by short_id),
         count(*) filter (where map_digest=v_old)
    into v_count,v_actual_ids,v_old_count
    from ops.rule_delivery_activation_target;
  if v_count<>cardinality(v_ids) or v_actual_ids is distinct from v_ids
     or v_old_count<>cardinality(v_ids) then
    raise exception '0863 REFUSED: exact activation-target ids and map preimage are absent';
  end if;
  update ops.rule_delivery_activation_target set map_digest=v_new
    where map_digest=v_old;
  get diagnostics v_count=row_count;
  if v_count<>cardinality(v_ids) then
    raise exception '0863 REFUSED: changed % targets, expected %',v_count,cardinality(v_ids);
  end if;
end $rule_delivery_0863$;
