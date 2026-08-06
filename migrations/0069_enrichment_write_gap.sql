-- 0069_enrichment_write_gap.sql — the schema half of loop #199 (2026-08-06).
--
-- THE GAP, proven by the 2026-08-06 Outlook mining run: update-vendor's whitelist
-- is relationship fields only, NO verb can write a contact fact (cell, personal
-- email, title, city, county) onto an existing party, and two vendor rows riding
-- ONE party cannot be merged by confirm-merge (same-party refusal, by design —
-- party merge is a different operation). The miner correctly routed 8 verified
-- facts through record-finding, but nothing can promote evidence into live
-- contact data. This migration adds the two missing columns; the verbs
-- (update-party-contact, merge-vendor-rows, the update-vendor whitelist
-- extension) land in mcp-server/src/tools.js in the same commit.
--
-- FOUR OBJECTS:
--   1. party.cell — record-finding already has a 'cell' kind and the mail miner
--      surfaces cells routinely; the column never existed, so a verified cell had
--      no live field to be promoted into. phone stays the office line.
--   2. vendor.merged_into — the vendor-row counterpart of party.merged_into and
--      client.merged_into (0001). Three same-party pairs exist today
--      (V-GC-001+V-GC-013, V-MKT-001+V-MSC-024, T-004+T-040 — the third found by
--      this migration's own sweep, not by the loop). Merging them is
--      merge-vendor-rows, humanOnly; this is only the pointer the verb writes.
--   3. v_ref_index — the vendor branch's `merged` flag learns about vendor-level
--      tombstones: (p.merged_into is not null OR v.merged_into is not null).
--      Tombstones stay VISIBLE (the 0016/0056 posture: find shows where a merged
--      record went); only the flag changes. CREATE OR REPLACE is legal — column
--      list and order are byte-identical to 0056.
--   4. The three vendor render/report views stop showing merged rows:
--      v_export_vendors (vendors.xlsx would show the same vendor twice),
--      v_vendor_level_suggestion (a tombstone would double-count evidence),
--      v_vendor_needs_type (a tombstone would nag for a category it can never
--      get). v_export_vendors also renders Category as coalesce(validated slug
--      label, legacy free text) — sessions now write category_slug (0050's
--      validated field), and without the coalesce the xlsx would keep showing
--      "Target (not yet met)" after a human categorises the vendor.
--
-- WHAT THIS MIGRATION DOES NOT DO: it merges nothing. Nothing auto-merges, ever
-- (the Garabadian rule); the three known pairs wait for a human's
-- merge-vendor-rows call. The guard below asserts zero merged vendors at apply.

begin;

-- 1. party.cell -------------------------------------------------------------
alter table party add column if not exists cell text;
comment on column party.cell is
  'Mobile line, distinct from phone (office). Written by update-party-contact '
  'only — a narrow contact-facts verb; identity fields (name, org, npi, '
  'specialty) are deliberately out of its reach per rule 5d44d3f3. The '
  'placeholder rule applies: a CARR agent''s own number is never stored here.';

-- 2. vendor.merged_into ------------------------------------------------------
alter table vendor add column if not exists merged_into uuid references vendor(id);
create index if not exists vendor_merged_idx on vendor (merged_into)
  where merged_into is not null;
comment on column vendor.merged_into is
  'Vendor-row merge pointer, written only by merge-vendor-rows (humanOnly). '
  'Covers the case confirm-merge structurally cannot: two vendor rows riding '
  'ONE party (a party-level merge that moved role rows, or a double import). '
  'A row with merged_into set is a tombstone: excluded from renders and '
  'reports, still resolvable through v_ref_index with merged=true so a search '
  'learns where it went.';

-- 3. v_ref_index — vendor branch merged flag ---------------------------------
-- Byte-identical to 0056 except the vendor branch's merged expression.
create or replace view v_ref_index as
select 'lead'::text          as subject_type,
       l.id                  as subject_id,
       l.registry_ref        as ref,
       p.name                as display_name,
       org.name              as org_name,
       p.city                as city,
       p.specialty           as specialty,
       l.stage               as status,
       (p.merged_into is not null) as merged,
       null::text            as client_ref,
       p.id                  as party_id
  from lead l
  join party p on p.id = l.party_id
  left join party org on org.id = p.org_id
union all
select 'client', c.id, c.roster_ref, p.name, org.name, p.city, p.specialty, c.status,
       (coalesce(c.merged_into, p.merged_into) is not null),
       null::text,
       p.id
  from client c
  join party p on p.id = c.party_id
  left join party org on org.id = p.org_id
union all
select 'vendor', v.id, v.vendor_ref, p.name, org.name, p.city, p.specialty, v.stage,
       (p.merged_into is not null or v.merged_into is not null), null::text, p.id
  from vendor v
  join party p on p.id = v.party_id
  left join party org on org.id = p.org_id
union all
select 'deal', d.id, null::text, d.name, null::text, null::text, null::text, d.phase,
       false, c.roster_ref, null::uuid
  from deal d
  left join client c on c.id = d.client_id
union all
select 'party', p.id, p.ref, p.name, org.name, p.city, p.specialty, p.contact_state,
       (p.merged_into is not null), null::text, p.id
  from party p
  left join party org on org.id = p.org_id
 where p.deleted_at is null
   and not exists (select 1 from lead   l where l.party_id = p.id)
   and not exists (select 1 from client c where c.party_id = p.id)
   and not exists (select 1 from vendor v where v.party_id = p.id);

grant select on v_ref_index to carr_reader;

-- 4a. v_export_vendors — merged rows out, Category prefers the validated slug --
drop view v_export_vendors;
create view v_export_vendors as
select v.vendor_ref              as "ID",
       p.name                    as "Name",
       org.name                  as "Company",
       coalesce(vc.label, v.category) as "Category",
       array_to_string(v.verticals, ', ') as "Vertical",
       p.title                   as "Title",
       coalesce(v.owner_label, owner.display_name) as "Owner",
       vs.label                  as "Stage",
       lt.last_touch             as "Last Touch",
       na.description            as "Next Step",
       case when v.referral_active then 'Yes' when not v.referral_active then 'No' end
                                 as "Referral-active?",
       v.territory               as "Territory",
       p.state                   as "State",
       v.offers                  as "Offers",
       v.seeking                 as "Seeking",
       v.links_label             as "Links",
       v.rivalry_group           as "Rivalry Group",
       v.originated              as "Originated / Referred",
       p.phone                   as "Phone",
       p.email                   as "Email",
       v.intro_notes             as "Notes",
       case when v.enrich then 'Yes' when not v.enrich then 'No' end as "Enrich?",
       v.out_of_market           as "_out_of_market"
from vendor v
join party p on p.id = v.party_id
left join vendor_stage vs on vs.slug = v.stage
left join vendor_category vc on vc.slug = v.category_slug
left join party org on org.id = p.org_id
left join actor owner on owner.id = v.owner_id
left join next_action na on na.subject_type='vendor' and na.subject_id=v.id and na.status='open'
left join v_last_touch lt on lt.subject_type='vendor' and lt.subject_id=v.id
where v.merged_into is null;
grant select on v_export_vendors to carr_reader;

-- 4b. v_vendor_level_suggestion — merged rows out ----------------------------
-- Re-run of 0065's block with one added predicate in `scored`. DROP first for
-- symmetry with 0065's own reasoning (and so a future column insert stays legal).
drop view if exists v_vendor_level_suggestion;
create view v_vendor_level_suggestion as
with contact as (
  select v.id as vendor_id,
         count(*) filter (
           where a.kind in ('meeting','tour','loi','lease_signed','email_in','counter_received')
              or (a.kind in ('call','text') and a.connected is true)
         ) as two_way,
         count(*) filter (
           where a.kind = 'email_out'
              or (a.kind in ('call','text') and a.connected is not true)
         ) as attempts_only
    from vendor v
    left join activity a on a.vendor_id = v.id
   group by v.id),
value_moved as (
  select v.id as vendor_id,
         count(*) filter (where pl.via_party = v.party_id) as they_gave,
         count(*) filter (where pl.via_party in (select id from party
                                                  where name in ('Joe Bookout','Dell McCraney'))
                            and (pl.from_party = v.party_id or pl.to_party = v.party_id)) as we_gave
    from vendor v
    left join party_link pl on pl.kind in ('introduced','referred')
                           and (pl.via_party = v.party_id
                             or pl.from_party = v.party_id
                             or pl.to_party = v.party_id)
   group by v.id),
scored as (
  select v.vendor_ref,
         p.name,
         v.relationship_level as recorded,
         c.two_way, c.attempts_only, vm.they_gave, vm.we_gave,
         c.two_way + c.attempts_only + vm.they_gave + vm.we_gave as evidence_events,
         case
           when c.two_way + c.attempts_only + vm.they_gave + vm.we_gave = 0 then null
           when vm.they_gave > 1 and vm.we_gave > 1 then 3
           when vm.they_gave > 0 or  vm.we_gave > 0 then 2
           when c.two_way > 0                        then 1
           else 0
         end as suggested
    from vendor v
    join party p        on p.id = v.party_id
    join contact c      on c.vendor_id = v.id
    join value_moved vm on vm.vendor_id = v.id
   where v.disposition = 'active'
     and v.merged_into is null)
select s.vendor_ref,
       s.name,
       s.recorded,
       s.suggested,
       (s.recorded is not null and s.suggested is not null and s.recorded <> s.suggested)
                                                    as disagrees,
       case
         when s.evidence_events = 0        then 'no_evidence'
         when s.recorded is null           then 'unjudged_with_evidence'
         when s.recorded = s.suggested     then 'agrees'
         when s.suggested > s.recorded     then 'evidence_exceeds_recorded'
         else                                   'recorded_exceeds_evidence'
       end                                          as signal,
       s.evidence_events,
       s.two_way, s.attempts_only, s.they_gave, s.we_gave
  from scored s;
grant select on v_vendor_level_suggestion to carr_reader;

comment on view v_vendor_level_suggestion is
  'Recorded relationship level against what the evidence supports (0052/0053/0065 '
  'lineage; 0069 excludes merged vendor rows so a tombstone never double-counts '
  'its survivor''s evidence). Reports only — the level stays a human judgment.';

-- 4c. v_vendor_needs_type — merged rows out ----------------------------------
create or replace view v_vendor_needs_type as
select v.vendor_ref, p.name, p.title, v.category as old_value,
       v.is_target, v.relationship_level, v.territory
  from vendor v join party p on p.id = v.party_id
 where v.category_slug is null and v.disposition = 'active'
   and v.merged_into is null
 order by v.is_target desc, v.relationship_level desc nulls last, p.name;
grant select on v_vendor_needs_type to carr_reader;

commit;

-- Guards in their own transaction (0043 lesson) -------------------------------
do $$
declare
  n int; grantees int; vend int; vend_branch int; export_rows int;
begin
  -- the two columns landed
  select count(*) into n from information_schema.columns
   where table_name='party' and column_name='cell';
  if n <> 1 then raise exception '0069: party.cell missing'; end if;

  select count(*) into n from information_schema.columns
   where table_name='vendor' and column_name='merged_into';
  if n <> 1 then raise exception '0069: vendor.merged_into missing'; end if;

  -- nothing is merged at apply time — this migration moves no data
  select count(*) into n from vendor where merged_into is not null;
  if n <> 0 then raise exception '0069: expected zero merged vendors at apply, found %', n; end if;

  -- v_ref_index still emits one row per vendor (tombstone posture unchanged)
  select count(*) into vend from vendor;
  select count(*) into vend_branch from v_ref_index where subject_type='vendor';
  if vend <> vend_branch then
    raise exception '0069: v_ref_index vendor branch % rows, vendor table %', vend_branch, vend;
  end if;

  -- with zero merged rows, the export view must show every vendor
  select count(*) into export_rows from v_export_vendors;
  if export_rows <> vend then
    raise exception '0069: v_export_vendors % rows, expected % (nothing merged yet)', export_rows, vend;
  end if;

  -- the 0020/0056 grant posture survives the replace
  select count(distinct grantee) into grantees
    from information_schema.role_table_grants
   where table_name='v_ref_index' and privilege_type='SELECT'
     and grantee in ('carr_reader','carr_writer','carr_jobs');
  if grantees < 3 then
    raise exception '0069: v_ref_index select grants incomplete (% of 3)', grantees;
  end if;
end $$;
