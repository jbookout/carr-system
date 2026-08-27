-- 0350_rule_delivery_activation_digest_repin.sql
--
-- WR-000019 changes after migration 0348 repinned the reviewed activation
-- overlay without changing the nine target rules. Refresh only the exact
-- immutable target preimage so the guarded cutover compares against the
-- current reviewed map. Any same-ID field drift refuses before mutation.

begin;

do $$
declare
  v_expected constant text := 'b513180786cf7212877870ab3bc14c03bb78b17b3397eb6ee474187a152b13f2';
  v_prior constant text := '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218';
  v_ids constant text[] := array[
    '25fcddee','3fa17fa0','72e06bdf','581cb3fe','113b3833',
    '57d13061','c66dc739','49533583','557838a5'
  ];
  v_updated bigint;
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0350 REFUSED: activation digest repin requires shadow mode';
  end if;

  update ops.rule_delivery_activation_target t
     set map_digest = v_expected
   where t.short_id = any(v_ids)
     and (t.short_id, t.expected_scope, t.expected_pack) in (
           values
             ('25fcddee','shared','governance-rules'),
             ('3fa17fa0','shared','client-deal'),
             ('72e06bdf','shared','client-deal'),
             ('581cb3fe','shared','delegation-council'),
             ('113b3833','joe','governance-rules'),
             ('57d13061','joe','joe-comms'),
             ('c66dc739','joe','joe-comms'),
             ('49533583','joe','joe-comms'),
             ('557838a5','joe','joe-comms')
         )
     and t.from_control = 'session_boot'
     and t.from_enforcement_class = 'surfacing'
     and t.from_implementation_ref =
           'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
     and t.from_test_ref = 'command:python3 hooks/gate-integrity.py --selftest'
     and t.map_digest = v_prior
     and t.to_control = 'pack_delivery'
     and t.to_enforcement_class = 'stop_gate'
     and t.to_implementation_ref =
           'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
     and t.to_test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py';

  get diagnostics v_updated = row_count;
  if v_updated <> cardinality(v_ids) then
    raise exception
      '0350 REFUSED: expected nine exact reviewed activation preimage rows, updated %',
      v_updated;
  end if;

  if (select count(*) from ops.rule_delivery_activation_target)
       <> cardinality(v_ids)
     or exists (
       select 1
         from ops.rule_delivery_activation_target t
        where t.short_id <> all(v_ids)
           or (t.short_id, t.expected_scope, t.expected_pack) not in (
                values
                  ('25fcddee','shared','governance-rules'),
                  ('3fa17fa0','shared','client-deal'),
                  ('72e06bdf','shared','client-deal'),
                  ('581cb3fe','shared','delegation-council'),
                  ('113b3833','joe','governance-rules'),
                  ('57d13061','joe','joe-comms'),
                  ('c66dc739','joe','joe-comms'),
                  ('49533583','joe','joe-comms'),
                  ('557838a5','joe','joe-comms')
              )
           or t.from_control <> 'session_boot'
           or t.from_enforcement_class <> 'surfacing'
           or t.from_implementation_ref <>
                'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
           or t.from_test_ref <>
                'command:python3 hooks/gate-integrity.py --selftest'
           or t.map_digest <> v_expected
           or t.to_control <> 'pack_delivery'
           or t.to_enforcement_class <> 'stop_gate'
           or t.to_implementation_ref <>
                'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
           or t.to_test_ref <>
                'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
     ) then
    raise exception '0350 FAILED: activation digest repin did not leave the exact reviewed nine';
  end if;
end $$;

commit;
