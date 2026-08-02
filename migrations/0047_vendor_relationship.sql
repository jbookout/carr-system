-- 0047_vendor_relationship.sql — vendor stage becomes three honest axes.
--
-- Joe: "vendors should also have a contact level - 0 (they are a prospective vendor)
-- 1 (minimal contact) 2 (decent relationship) 3 (our best relationships)... feel free to
-- iterate a better naming convention." He then took the iterated names: Prospective,
-- Building, Established, Core.
--
-- ── CORRECTION TO 0046, which cannot be edited (applied, sha-checked) ──
-- 0046 line 99 reads "guards INSIDE the transaction (0043 lesson...)" and the guards sit
-- AFTER its commit, outside. The comment asserts something the code does not do — the exact
-- defect this whole audit is about, in a migration that cites the lesson in its own header.
-- Third occurrence in one day: noticed on 0033/0035, fixed and written up on 0043, repeated
-- on 0046. Nothing is stranded (0046's guards passed), but the comment is false. THIS file
-- has its guard block before commit, where the comment says it is.
--
-- ── WHY THE OLD VOCABULARY HAD TO GO ──
-- Nine values across 290 vendors, doing THREE different jobs at once:
--   prospect_uncontacted      118   relationship depth 0
--   unrated                    59   NOT a depth — nobody has judged them yet
--   target_not_yet_met         41   depth 0 PLUS "we want this one"
--   decent_relationship        37   depth 2
--   building_working_on_it     17   depth 1
--   important_strengthening     8   depth 3
--   avoid_not_a_good_partner    7   a RULING, not a depth
--   fully_aligned               2   depth 3
--   tabled_parked_drip_only     1   a RULING
-- Cramming a ruling into a stage is precisely the 0045 fault (Tubbs sat on a prospecting
-- drip because his campaign field held a decision that his other three fields contradicted).
-- Same fix as loop domains and contact type: separate the axes.
--
--   relationship_level  0-3, ordinal, sortable. NULL = not yet judged.
--   disposition         active | avoid | parked. The ruling.
--   is_target           Y/N. "target_not_yet_met" is depth 0 plus intent; folding intent
--                       into the level would lose what 41 vendors were flagged FOR.
--
-- ── NULL IS NOT ZERO, and 59 vendors depend on the distinction ──
-- "We have not judged this vendor" and "we judged them and they are prospective" are
-- different facts. Collapsing `unrated` into 0 would bury the finding that ONE IN FIVE
-- vendors has never been assessed. Same reasoning that left loop.domain nullable this
-- afternoon: a guessed classification hides exactly what the column exists to surface.

begin;

create table if not exists vendor_relationship_level (
  level integer primary key,
  label text not null,
  note  text not null
);

insert into vendor_relationship_level (level, label, note) values
  (0, 'Prospective', 'Identified as worth knowing. Not yet met.'),
  (1, 'Building',    'Contact made. Early, still establishing whether it works both ways.'),
  (2, 'Established', 'A real working relationship. Business flows or plausibly will.'),
  (3, 'Core',        'Best relationships. Reciprocal, reliable, worth protecting.')
on conflict (level) do nothing;

create table if not exists vendor_disposition (
  slug text primary key, label text not null, workable boolean not null, sort integer not null unique
);
insert into vendor_disposition (slug, label, workable, sort) values
  ('active', 'Active — in the network',                       true,  10),
  ('parked', 'Parked — deliberately dormant, drip only',      false, 20),
  ('avoid',  'Avoid — ruled out as a partner, with a reason', false, 30)
on conflict (slug) do nothing;

alter table vendor add column if not exists relationship_level integer
  references vendor_relationship_level(level);
alter table vendor add column if not exists disposition text
  references vendor_disposition(slug) default 'active';
alter table vendor add column if not exists is_target boolean not null default false;

-- Map the nine stages onto the three axes. stage is LEFT IN PLACE, unread but intact:
-- nothing is deleted while the new columns are still being trusted.
update vendor set relationship_level = 0 where stage in ('prospect_uncontacted','target_not_yet_met');
update vendor set relationship_level = 1 where stage = 'building_working_on_it';
update vendor set relationship_level = 2 where stage = 'decent_relationship';
update vendor set relationship_level = 3 where stage in ('important_strengthening','fully_aligned');
-- unrated, avoid_not_a_good_partner and tabled_parked_drip_only keep relationship_level
-- NULL: none of them states a depth, and inventing one would be a fabricated fact.

update vendor set is_target = true where stage = 'target_not_yet_met';

update vendor set disposition = 'avoid'  where stage = 'avoid_not_a_good_partner';
update vendor set disposition = 'parked' where stage = 'tabled_parked_drip_only';
update vendor set disposition = 'active' where disposition is null;

alter table vendor alter column disposition set not null;
create index if not exists vendor_relationship_idx on vendor (relationship_level, disposition);

comment on column vendor.relationship_level is
  '0 Prospective / 1 Building / 2 Established / 3 Core (vendor_relationship_level). NULL = '
  'NOT YET JUDGED, which is a different fact from 0 and must stay distinguishable: 59 of '
  '290 vendors were `unrated` at 0047 and collapsing them into 0 would have hidden that one '
  'in five has never been assessed.';
comment on column vendor.disposition is
  'active | parked | avoid. A RULING, deliberately not a relationship level — the 0045 '
  'fault was a decision stored in a stage field, where it contradicted three others.';
comment on column vendor.is_target is
  'We want this one. Separate from level because target_not_yet_met was depth 0 PLUS intent, '
  'and folding intent into the level loses what 41 vendors were flagged for.';

-- guards, genuinely before commit this time
do $$
declare unmapped int; lvl0 int; lvl1 int; lvl2 int; lvl3 int; unjudged int;
        targets int; avoided int; parked int;
begin
  -- every vendor whose old stage stated a depth must now carry one
  select count(*) into unmapped from vendor
   where relationship_level is null
     and stage in ('prospect_uncontacted','target_not_yet_met','building_working_on_it',
                   'decent_relationship','important_strengthening','fully_aligned');
  if unmapped > 0 then
    raise exception '% vendor(s) had a stage stating a depth but took no level', unmapped;
  end if;

  select count(*) into lvl0 from vendor where relationship_level = 0;
  select count(*) into lvl1 from vendor where relationship_level = 1;
  select count(*) into lvl2 from vendor where relationship_level = 2;
  select count(*) into lvl3 from vendor where relationship_level = 3;
  select count(*) into unjudged from vendor where relationship_level is null;
  select count(*) into targets from vendor where is_target;
  select count(*) into avoided from vendor where disposition = 'avoid';
  select count(*) into parked  from vendor where disposition = 'parked';

  if lvl0 <> 159 then raise exception 'expected 159 at level 0 (118+41), got %', lvl0; end if;
  if lvl1 <> 17  then raise exception 'expected 17 at level 1, got %', lvl1;  end if;
  if lvl2 <> 37  then raise exception 'expected 37 at level 2, got %', lvl2;  end if;
  if lvl3 <> 10  then raise exception 'expected 10 at level 3 (8+2), got %', lvl3; end if;
  if targets <> 41 then raise exception 'expected 41 targets, got %', targets; end if;
  if avoided <> 7  then raise exception 'expected 7 avoid, got %', avoided; end if;
  if parked  <> 1  then raise exception 'expected 1 parked, got %', parked;  end if;

  -- the 7 ruled-out vendors must KEEP their ruling; losing it would put them back in play
  if exists (select 1 from vendor where stage = 'avoid_not_a_good_partner'
                                    and disposition <> 'avoid') then
    raise exception 'a vendor ruled avoid did not keep its disposition';
  end if;

  raise notice 'vendor axes live — 0:% 1:% 2:% 3:% unjudged:% | targets:% avoid:% parked:%',
               lvl0, lvl1, lvl2, lvl3, unjudged, targets, avoided, parked;
end $$;

commit;
