-- 0099_deal_stagnation_routes.sql
-- First allowlisted diagnostic neighborhood for the 0098 control plane.
-- These are questions the investigation may test, not conclusions it may assume.

begin;

insert into diagnostic_route
  (route_key, signal_kind, from_kind, relation, to_kind, test_verb,
   input_contract, minimum_effect, created_by)
select v.route_key, 'deal_stagnation', 'deal_stagnation', v.relation,
       v.to_kind, 'get-deal-room', v.input_contract, null, a.id
  from actor a
  cross join (values
    ('deal_stagnation.next_action_gap',
     'may_be_explained_by', 'next_action_gap',
     '{"deal":"signal.subject_ref","inspect":["next_actions","next_step","next_date"]}'::jsonb),
    ('deal_stagnation.relationship_inactivity',
     'may_be_explained_by', 'relationship_inactivity',
     '{"deal":"signal.subject_ref","inspect":["last_touch","activity","participants"]}'::jsonb),
    ('deal_stagnation.critical_date_pressure',
     'may_be_explained_by', 'critical_date_pressure',
     '{"deal":"signal.subject_ref","inspect":["critical_dates","documents","phase"]}'::jsonb)
  ) as v(route_key, relation, to_kind, input_contract)
 where a.slug = 'system'
on conflict (route_key) do nothing;

do $$
declare route_count int;
begin
  select count(*) into route_count
    from diagnostic_route
   where signal_kind='deal_stagnation' and from_kind='deal_stagnation' and active;
  if route_count <> 3 then
    raise exception '0099: expected three active deal-stagnation routes, found %', route_count;
  end if;
end $$;

commit;
