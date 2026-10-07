-- rollback: forward-only — a repin only moves the guarded map-digest preimage; a later repin migration moves it again
-- Registering the github-burst-guard control changes the reviewed map's byte
-- digest (ops/config/rule-enforcement-map.json gains one control_catalog entry;
-- no rule text, delivery mode, approval or rule-to-control binding moves). The
-- pack cutover contracts stay the same; move their identity forward without
-- changing anything else, exactly as 0837 did for the stale_claim retirement.
do $rule_delivery_0863$
declare
  v_old constant text := 'b4e0d6689df3d96be24fb0cb888587f545bef8ffeba79c3ee6757b447f3fb308';
  v_new constant text := '46ef9935803244ea4b64f5e528148b89199995e7fc3c24323c4de8b2c8474921';
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
