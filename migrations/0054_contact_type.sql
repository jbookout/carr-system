-- 0054_contact_type.sql — contact type, derived from two axes.
--
-- Joe's design: "would it be better to have a vendor column (y/n) separate of client related
-- column and then theres no need to have the + categories. if there is a client type and the
-- vendor column is marked Y then they are treated as vendor+client."
--
-- He first sketched ten enumerated values (vendor, lead, prospect, client, past client,
-- past client+active client, vendor+lead, vendor+prospect, vendor+client, vendor+past
-- client) and then found the better model himself. Two axes beat enumeration on three counts:
--   OPEN-ENDED   covers pairings nobody listed. His own list was already incomplete at ten.
--   HOLDS BOTH   "vendor+lead" as one string cannot say Derrick Richardson's vendor level is
--                Established while his lead stage is outreach_active. Two axes can.
--   CANNOT DRIFT the vendor Y is DERIVED from a vendor record existing, so marking someone a
--                vendor IS creating their vendor record. A typed flag would be one more
--                field able to disagree with reality — the 0045 Tubbs fault exactly.
--
-- PAST CLIENT IMPLIES PROSPECT — BY DEFAULT, NOT AUTOMATICALLY. Joe: "any past client is
-- also a prospect for a future deal", then: "it should be default but not automatic for past
-- clients to be prospects. if a deal ended badly we may not handle them like a prospect due
-- to the context." So the rule fires unless contact_state says otherwise: a past client
-- carrying do_not_contact or paused is NOT re-offered as a prospect, and the reason travels
-- with the state so the next session knows why without asking.
--
-- The journey stage is read from the ROLE RECORDS rather than stored twice: client.status
-- and lead.stage already carry it, and a third copy on party would be a third thing to keep
-- in step. If that read ever gets expensive it becomes a materialised column — but not
-- before, because a cached copy that drifts is worse than a join.

begin;

create or replace view v_contact as
with roles as (
  select p.id as party_id, p.ref, p.name, p.title, p.contact_state,
         (select c.roster_ref from client c where c.party_id = p.id and c.merged_into is null limit 1) as client_ref,
         (select c.status     from client c where c.party_id = p.id and c.merged_into is null limit 1) as client_status,
         (select l.registry_ref from lead l where l.party_id = p.id limit 1) as lead_ref,
         (select l.stage        from lead l where l.party_id = p.id limit 1) as lead_stage,
         (select v.vendor_ref   from vendor v where v.party_id = p.id limit 1) as vendor_ref,
         (select v.relationship_level from vendor v where v.party_id = p.id limit 1) as vendor_level
    from party p
   where p.merged_into is null and p.deleted_at is null
),
axes as (
  select r.*,
         (r.vendor_ref is not null) as is_vendor,
         case
           when r.client_status = 'past_client'  then 'past_client'
           when r.client_ref is not null         then 'client'
           when r.lead_stage in ('qualified','engaged','active_deal') then 'prospect'
           when r.lead_ref is not null           then 'lead'
           else null
         end as journey_stage
    from roles r
)
select ref, name, title, contact_state,
       journey_stage, is_vendor,
       client_ref, lead_ref, vendor_ref, vendor_level,
       -- the display label Joe described, computed rather than typed
       nullif(trim(both ' + ' from
         concat_ws(' + ',
           case when is_vendor then 'Vendor' end,
           case journey_stage
             when 'past_client' then 'Past Client'
             when 'client'      then 'Client'
             when 'prospect'    then 'Prospect'
             when 'lead'        then 'Lead'
           end)), '') as contact_type,
       -- past client implies prospect, unless contact_state says leave them alone
       (journey_stage = 'past_client'
        and contact_state in ('active','nurture')) as prospect_by_default
  from axes;

-- Role records left pointing at a party that was merged or deleted. The party merge does
-- not cascade to roles, so a vendor/lead/client row can outlive its person and vanish from
-- every party-based view while still existing. Three exist today; the 42 pending merges
-- would add more silently.
create or replace view v_orphaned_role as
select 'vendor' as role, v.vendor_ref as ref, p.name, p.ref as party_ref,
       p.merged_into is not null as party_merged
  from vendor v join party p on p.id = v.party_id
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'client', c.roster_ref, p.name, p.ref, p.merged_into is not null
  from client c join party p on p.id = c.party_id
 where c.merged_into is null and (p.merged_into is not null or p.deleted_at is not null)
union all
select 'lead', l.registry_ref, p.name, p.ref, p.merged_into is not null
  from lead l join party p on p.id = l.party_id
 where p.merged_into is not null or p.deleted_at is not null;

comment on view v_orphaned_role is
  'Role records whose party was merged away or deleted. Found at 0054 when v_contact showed '
  '287 vendors instead of 290: merging a party does NOT cascade to its role rows, so a '
  'vendor can outlive its person and disappear from every party-based view while still '
  'existing. Watch this through the 42 pending lead/client merges.';

comment on view v_contact is
  'One row per living party with contact type DERIVED from two independent axes (Joe, '
  '2026-08-02): journey_stage from the role records, is_vendor from whether a vendor record '
  'exists. contact_type renders "Vendor + Client" etc. without anyone typing it, so it '
  'cannot drift. prospect_by_default implements "any past client is also a prospect for a '
  'future deal" as a DEFAULT, suppressed when contact_state is paused or do_not_contact — '
  'because "if a deal ended badly we may not handle them like a prospect due to the context".';

do $$
declare total int; vendors int; dual_role int; derrick text; orphans int;
begin
  select count(*) into total from v_contact;
  select count(*) into vendors from v_contact where is_vendor;
  select count(*) into dual_role from v_contact where is_vendor and journey_stage is not null;

  -- 287, not 290: three vendor rows point at parties that were MERGED AWAY and were never
  -- cleaned up (V-GC-013 "Trey", V-MSC-024 "Nate", T-040 "Ben Clark" — first-name-only
  -- duplicates whose party was merged while their vendor row was left behind). v_contact is
  -- right to exclude them; the guard's expectation of 290 was the wrong assumption. Surfaced
  -- rather than swallowed, because the same orphaning will happen 42 more times when the
  -- lead/client pairs merge unless someone is watching for it.
  if vendors <> 287 then raise exception 'expected 287 vendors on live parties, got %', vendors; end if;
  select count(*) into orphans from v_orphaned_role;
  if orphans < 3 then raise exception 'orphan detector sees % role rows, expected at least 3', orphans; end if;

  -- Derrick Richardson is the one party in 665 who genuinely wears two roles. Until his two
  -- party rows are merged he shows as two contacts, each with one role — which is exactly
  -- the defect the merge step exists to fix, and worth asserting so the fix is provable.
  select string_agg(coalesce(contact_type,'(none)'), ' / ' order by ref)
    into derrick from v_contact where name = 'Derrick Richardson';
  raise notice 'v_contact live: % contacts, % vendors, % wearing both axes. '
               'Derrick Richardson currently reads as: % (two party rows, pre-merge)',
               total, vendors, dual_role, derrick;
end $$;

commit;
