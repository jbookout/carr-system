-- 0052_vendor_level_triggers.sql — the relationship levels get measurable triggers.
--
-- 0047 created the levels; Joe asked the question that makes them real: "what makes a
-- relationship go from 0 to 1? is it simply making an attempt to contact? or actually
-- meeting with them? we need to establish measureables for each level."
--
-- The answer he approved defines each level by a COUNTABLE EVENT, not a feeling:
--   0 Prospective  identified, no two-way contact
--   1 Building     first TWO-WAY contact — they replied, or you met. An outbound attempt
--                  alone does NOT count: you can email fifty people and have fifty
--                  relationships that do not exist.
--   2 Established  value has moved in EITHER direction — an intro, a referral, a deal
--   3 Core         reciprocal AND repeated — value both ways, more than once
--
-- WHY THIS MATTERS BEYOND TIDINESS: 2 and 3 become measurable from the intro graph the
-- moment it has data (0051). The system can then say "this vendor sits at Core and nothing
-- has passed between you in eight months" — or the reverse, that someone at Building has
-- quietly sent three referrals.
--
-- STORED, WITH A DERIVED SUGGESTION. The level stays Joe's judgment; the view reports where
-- the evidence disagrees. Same posture as v_drip_conflict and v_loop_bell_cap: report,
-- never enforce. A level that overwrote itself from event counts would be wrong the first
-- time a relationship mattered for a reason the database cannot see.
--
-- ONE HONEST LIMIT ON "TWO-WAY": activity has no direction column, so two-way is read from
-- the KIND — a meeting or a call is inherently two-way, and an inbound email proves they
-- replied. An outbound-only history reads as level 0, which is the intent.

begin;

update vendor_relationship_level set note = case level
  when 0 then 'Identified as worth knowing. No two-way contact yet. TRIGGER OUT: they reply, or you meet.'
  when 1 then 'First TWO-WAY contact has happened — they replied or you met. An outbound attempt alone does not count. TRIGGER OUT: value moves in either direction (an intro, a referral, a deal).'
  when 2 then 'Value has moved in EITHER direction at least once. TRIGGER OUT: value moves BOTH ways, more than once.'
  when 3 then 'Reciprocal and repeated — value both ways, more than once. Protect these.'
  end;

-- What the evidence suggests, against what is recorded.
create or replace view v_vendor_level_suggestion as
with contact as (
  select v.id as vendor_id,
         count(*) filter (where a.kind in ('meeting','call','text','email_in')) as two_way,
         count(*) filter (where a.kind = 'email_out')                            as outbound_only
    from vendor v left join activity a on a.vendor_id = v.id
   group by v.id),
value_moved as (
  select v.id as vendor_id,
         count(*) filter (where pl.via_party = v.party_id) as they_gave,
         count(*) filter (where pl.via_party in (select id from party where name in ('Joe Bookout','Dell McCraney'))
                            and (pl.from_party = v.party_id or pl.to_party = v.party_id)) as we_gave
    from vendor v
    left join party_link pl on pl.kind in ('introduced','referred')
                           and (pl.via_party = v.party_id or pl.from_party = v.party_id or pl.to_party = v.party_id)
   group by v.id)
select v.vendor_ref, p.name, v.relationship_level as recorded,
       case
         when vm.they_gave > 1 and vm.we_gave > 1 then 3
         when vm.they_gave > 0 or  vm.we_gave > 0 then 2
         when c.two_way > 0                        then 1
         else 0
       end as suggested,
       c.two_way, c.outbound_only, vm.they_gave, vm.we_gave
  from vendor v
  join party p on p.id = v.party_id
  join contact c on c.vendor_id = v.id
  join value_moved vm on vm.vendor_id = v.id
 where v.disposition = 'active';

comment on view v_vendor_level_suggestion is
  'Recorded relationship level against what the evidence supports, per the triggers Joe '
  'approved 2026-08-02. Reports only — the level stays a human judgment, because a '
  'relationship can matter for reasons no event count can see. Two-way contact is read from '
  'activity KIND (meeting/call/text/email_in), since activity has no direction column; an '
  'outbound-only history reads as 0, which is the point.';

do $$
declare noted int; disagreements int;
begin
  select count(*) into noted from vendor_relationship_level where note like '%TRIGGER%' or level = 3;
  if noted <> 4 then raise exception 'expected all 4 levels to carry triggers, got %', noted; end if;

  select count(*) into disagreements from v_vendor_level_suggestion where recorded is distinct from suggested;
  raise notice 'level triggers recorded. % active vendors where evidence disagrees with the '
               'recorded level (expected to be high until capture starts)', disagreements;
end $$;

commit;
