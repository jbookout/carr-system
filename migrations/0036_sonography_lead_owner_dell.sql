-- 0036_sonography_lead_owner_dell.sql — Sonography Studios is Dell's deal.
--
-- Joe, 2026-08-02: "sonograph studios is dells why tf do you keep missing that"
--                  "yea its dells deal and its in the legal phase"
--
-- WHY IT KEPT GETTING RE-REPORTED AS AN UNKNOWN. It was never an unknown — Joe has said
-- so more than once. It persisted because the record layer has NO WAY TO WRITE IT.
-- v_deal_board derives lead_owner from a deal_participant row (role='lead', to_at null),
-- and update-deal's allow-list is exactly:
--     phase, segment, outcome, closed_on, won_value, notes_path
-- lead_owner is absent, and no other verb inserts a deal_participant. So every audit read
-- lead_owner:null, reported it as a gap, and had no means to close it. A fact the human
-- has stated repeatedly and the system cannot record is a missing verb, not a missing fact.
--
-- Sonography Studios – Lily Frank PCB (C-158) is the ONLY deal of 38 with no lead. Its
-- phase already reads 'legal' and is correct, so nothing here touches phase.
--
-- set_by is joe: he is the one who stated the assignment. actor is dell: he owns the work.
-- That distinction is the same one the on_behalf_of gap (loop #124) exists to make
-- everywhere else.
--
-- FOLLOW-UP, deliberately NOT done here: add lead_owner to update-deal so the next
-- reassignment is a verb call and not a migration. That is a Worker change and a deploy,
-- and it is not going in the night before Dell's migration. Tracked separately.

begin;

-- Matched by id, not name: the name carries an en-dash and would break on any
-- encoding drift. Deal 12faa5d3 = Sonography Studios – Lily Frank PCB (client C-158).
insert into deal_participant (deal_id, actor_id, role, from_at, to_at, set_by)
select d.id,
       (select id from actor where slug = 'dell'),
       'lead',
       now(),
       null,
       (select id from actor where slug = 'joe')
  from deal d
 where d.id = '12faa5d3-6ff2-49d7-af99-af1d47551ba8'
   and not exists (
     select 1 from deal_participant dp
      where dp.deal_id = d.id and dp.role = 'lead' and dp.to_at is null
   );

commit;

-- guard: the deal now has exactly one current lead, it is Dell, and no OTHER deal
-- lost or gained one.
do $$
declare owner_slug text; ownerless int;
begin
  select lead_owner into owner_slug from v_deal_board
   where id = '12faa5d3-6ff2-49d7-af99-af1d47551ba8';

  if owner_slug is null then
    raise exception 'Sonography still has no current lead — the insert matched no deal (name drift?)';
  end if;
  if owner_slug <> 'dell' then
    raise exception 'Sonography lead resolved to %, expected dell', owner_slug;
  end if;

  select count(*) into ownerless from v_deal_board where lead_owner is null;
  if ownerless <> 0 then
    raise exception '% deal(s) still have no lead owner — expected 0', ownerless;
  end if;

  raise notice 'Sonography Studios lead owner: %; deals without an owner: 0', owner_slug;
end $$;
