-- 0050_vendor_category.sql — vendor type becomes a controlled vocabulary, and stops lying.
--
-- Joe: "vendor type should never be misc. if they are a rare type they deserve their own
-- new category so we can recall that data in the future without missing anyone."
--
-- TWO OF THE SIXTEEN "CATEGORIES" ARE NOT TYPES AT ALL:
--   Misc                   22   the value Joe is ruling out
--   Target (not yet met)   41   a STAGE wearing a type's clothes
-- 63 of 290 vendors (22%) therefore have no usable profession recorded. The 41 are lenders
-- or CPAs or contractors — nobody knows which, because the field was used for status. That
-- status now lives correctly in is_target (0047), so the slot is free to hold the truth.
--
-- category is FREE TEXT today, which is exactly how a stage got into it. It becomes a ref
-- table with a foreign key, so the next status value cannot be typed into it.
--
-- THE 63 GO TO NULL, NOT TO A PLACEHOLDER. "Not yet categorised" is a true statement;
-- "Misc" is a false one that hides the gap, and hiding it is how 22 vendors sat
-- uncategorised without anyone noticing. A profession cannot be inferred from a record —
-- that is Joe's or Dell's knowledge — so this migration makes the gap impossible to lose
-- rather than pretending to fill it. v_vendor_needs_type carries them until they are named.
--
-- ADDING A CATEGORY IS A ROW, NOT A MIGRATION — the point of the ref table, and what makes
-- "rare types deserve their own category" cheap enough to actually happen.

begin;

create table if not exists vendor_category (
  slug text primary key,
  label text not null,
  sort integer not null unique
);

insert into vendor_category (slug, label, sort) values
  ('lender',        'Banker / Lender',              10),
  ('cpa',           'CPA / Financial',              20),
  ('attorney',      'Attorney',                     30),
  ('broker',        'Practice Broker / Consultant', 40),
  ('gc',            'General Contractor',           50),
  ('architect',     'Architect / Design',           60),
  ('supply',        'Supply / Equipment Rep',       70),
  ('insurance',     'Insurance',                    80),
  ('it',            'IT Services',                  90),
  ('marketing',     'Marketing / Demographics',    100),
  ('developer',     'Developer / Investor',        110),
  ('sbdc',          'SBDC Consultant',             120),
  ('franchise',     'Franchise Development',       130),
  ('doctor',        'Doctor (networking)',         140)
on conflict (slug) do nothing;

alter table vendor add column if not exists category_slug text references vendor_category(slug);

update vendor set category_slug = case category
  when 'Banker / Lender'              then 'lender'
  when 'CPA / Financial'              then 'cpa'
  when 'Attorney'                     then 'attorney'
  when 'Practice Broker / Consultant' then 'broker'
  when 'General Contractor'           then 'gc'
  when 'Architect / Design'           then 'architect'
  when 'Supply / Equipment Rep'       then 'supply'
  when 'Insurance'                    then 'insurance'
  when 'IT Services'                  then 'it'
  when 'Marketing / Demographics'     then 'marketing'
  when 'Developer / Investor'         then 'developer'
  when 'SBDC Consultant'              then 'sbdc'
  when 'Franchise Development'        then 'franchise'
  when 'Doctor (networking)'          then 'doctor'
  else null end;

create index if not exists vendor_category_slug_idx on vendor (category_slug);

-- The uncategorised, kept visible until a human names them.
create or replace view v_vendor_needs_type as
select v.vendor_ref, p.name, p.title, v.category as old_value,
       v.is_target, v.relationship_level, v.territory
  from vendor v join party p on p.id = v.party_id
 where v.category_slug is null and v.disposition = 'active'
 order by v.is_target desc, v.relationship_level desc nulls last, p.name;

comment on view v_vendor_needs_type is
  'Active vendors with no real category. 63 at 0050: 22 were "Misc" and 41 were "Target '
  '(not yet met)", a stage stored in a type field. A profession cannot be inferred from a '
  'record, so these wait for Joe or Dell rather than being guessed. Sorted targets-first '
  'and deepest-relationship-first, because those are the ones whose type is worth knowing '
  'soonest.';

comment on column vendor.category_slug is
  'FK to vendor_category. Replaces the free-text `category`, which is how a stage value '
  '("Target (not yet met)", 41 rows) got stored as a profession. NULL means NOT YET '
  'CATEGORISED — never "Misc", which is a false statement that hid 22 vendors. Adding a '
  'rare type is an INSERT here, not a migration, which is what makes Joe''s "they deserve '
  'their own category" affordable.';

do $$
declare mapped int; unmapped int; cats int;
begin
  select count(*) into cats from vendor_category;
  if cats <> 14 then raise exception 'expected 14 categories, found %', cats; end if;

  select count(*) into mapped   from vendor where category_slug is not null;
  select count(*) into unmapped from vendor where category_slug is null;
  if mapped <> 227 then raise exception 'expected 227 mapped (290-63), got %', mapped; end if;
  if unmapped <> 63 then raise exception 'expected 63 uncategorised, got %', unmapped; end if;

  -- nobody may have been silently filed as Misc again
  if exists (select 1 from vendor_category where slug in ('misc','other','target')) then
    raise exception 'a catch-all category was seeded — the whole point is that there is none';
  end if;

  raise notice 'vendor categories: % mapped, % awaiting a real type (v_vendor_needs_type)',
               mapped, unmapped;
end $$;

commit;
