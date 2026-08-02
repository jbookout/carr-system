-- 0053_activity_connected.sql — did the contact actually CONNECT?
--
-- Joe, on the level-1 trigger: "as long as you have the ability to distinguish outreach
-- attempts". Checked, and the honest answer was: only partly.
--
--   direction explicit    email_out/email_in, counter_sent/counter_received
--   inherently two-way    meeting, tour, loi, lease_signed — you cannot do these alone
--   AMBIGUOUS             call, text
--
-- A logged `call` is either a twenty-minute conversation or a voicemail. A `text` may never
-- have been answered. activity_kind carries only slug/label/is_contact — no direction, no
-- outcome. So v_vendor_level_suggestion (0052) counted every call and text as two-way and
-- would have promoted a vendor to Building on an unreturned voicemail: exactly the failure
-- the trigger exists to prevent, shipped inside the migration that defined it.
--
-- THE FIX IS A FACT, NOT AN INFERENCE. `connected` records whether the contact reached the
-- person. It cannot be derived — only whoever made the call knows — so it is stored, and
-- NULL means "not recorded" rather than "no". The suggestion view then counts a call or
-- text ONLY when connected is explicitly true. Conservative on purpose: under-promoting a
-- relationship is a small error, while promoting on a voicemail corrupts the one number
-- Joe uses to judge his network.
--
-- Meetings, tours, LOIs and signed leases stay two-way without needing the flag: attendance
-- is the proof. Backfilling `connected` for existing calls would be inventing an outcome
-- nobody recorded, so nothing is backfilled — the standing no-fabrication rule.

begin;

alter table activity add column if not exists connected boolean;

comment on column activity.connected is
  'Did this contact actually reach the person? TRUE = spoke/replied, FALSE = attempt only '
  '(voicemail, no answer, unanswered text), NULL = not recorded. Only meaningful for kinds '
  'whose direction is ambiguous — call and text. meeting/tour/loi/lease_signed are two-way '
  'by definition; email_in and counter_received prove a reply by existing. Stored because '
  'only the person who made the call knows, and NULL is honestly unknown rather than no.';

-- Rebuild the suggestion so an attempt never counts as a relationship.
-- DROP first: Postgres refuses to rename a view column (outbound_only -> attempts_only)
-- through CREATE OR REPLACE.
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
       c.two_way, c.attempts_only, vm.they_gave, vm.we_gave
  from vendor v
  join party p on p.id = v.party_id
  join contact c on c.vendor_id = v.id
  join value_moved vm on vm.vendor_id = v.id
 where v.disposition = 'active';

comment on view v_vendor_level_suggestion is
  'Recorded relationship level against what the evidence supports (triggers approved by Joe '
  '2026-08-02). A call or text counts as two-way ONLY when activity.connected is explicitly '
  'true — an unreturned voicemail is an attempt, not a relationship. Reports only; the level '
  'stays a human judgment, because a relationship can matter for reasons no event count sees.';

do $$
declare ambiguous int; promoted_on_attempt int;
begin
  select count(*) into ambiguous from activity
   where kind in ('call','text') and connected is null;

  -- the whole point: nobody may be suggested level 1+ on attempts alone
  select count(*) into promoted_on_attempt from v_vendor_level_suggestion
   where suggested >= 1 and two_way = 0;
  if promoted_on_attempt > 0 then
    raise exception '% vendor(s) suggested at level 1+ with zero two-way contact — an '
                    'attempt is being counted as a relationship', promoted_on_attempt;
  end if;

  raise notice 'connected recorded on activity. % call/text row(s) have no outcome yet and '
               'are counted as attempts, not contact (nothing backfilled by design)', ambiguous;
end $$;

commit;
