-- 0074_deal_city_lane.sql — finish what 0061 started: give `city` a real column
-- and put BOTH city and lane on the export view. Joe's ruling, 2026-08-07.
--
-- WHAT 0061 ALREADY DID, so this file does not repeat it. The national-account
-- migration (the Musicologie ruling, 2026-08-02) created `deal_lane`, added
-- `deal.lane`, and backfilled it verbatim from source_row->>'lane'. That half is
-- DONE: 25 territory, 15 national at time of writing. A first draft of this file
-- tried to create deal_lane again and died on `relation "deal_lane" already
-- exists` — caught on a rehearsal branch, which is exactly what A14 is for.
--
-- WHAT WAS STILL MISSING, and why it mattered:
--
--   1. `city` never became a column at all. It lives only inside
--      `deal.source_row`, the preserved copy of the original imported row, so no
--      verb can set it. update-deal's allowed list had no city, which is why the
--      2026-08-07 Salesforce read could only REPORT Erik Petersen's move
--      ("Miramar Beach / Walton Co" -> "Santa Rosa Beach") and never apply it.
--
--   2. Neither city NOR lane was exposed on `v_export_deals`. The exporter and
--      lib/record_sources.py therefore read both out of the source_row JSON,
--      which means a deal with no source_row has neither. Proved minutes after
--      the new `new-deal` verb went live: 'James Allen Tyrer', the first deal
--      ever created by a verb rather than by the bulk import, appeared in
--      salesforce-diff under CHANGED with "city: (blank) -> Moultrie" and
--      "lane: (unset) -> territory" and no possible remedy. Five more new deals
--      were queued behind it. A CHANGED section full of diffs nobody can act on
--      is one people stop reading, which is how a real drift gets missed.
--
-- LANE TRUTH IS UNCHANGED. The authority is still Salesforce's "Out of Market
-- Deal" checkbox, read per-run by salesforce-diff, never inferred from the city
-- string. This gives the answer a home; it does not make the column the source.
--
-- SOURCE_ROW IS NOT TOUCHED, EVER. The JSON keeps its verbatim keys; the columns
-- become the read path and both stay true. exporters/targets.py and
-- lib/record_sources.py are updated in the same commit so the DB wins for these
-- two fields, the same fidelity rule already applied to name/phase/owner.

begin;

-- ---------- the one missing column ----------
-- `if not exists` for the same reason 0061 used it: forward-only migrations are
-- re-read by humans and re-run on branches, and a second run must not explode.
alter table deal add column if not exists city text;

comment on column deal.city is
  'City of transaction (0074). Transcribed from deal.source_row->>''city'', which the '
  'Salesforce import has always carried and the importer discarded. Before this column '
  'existed no verb could set a city, so salesforce-diff could report a city move but never '
  'apply one, and any deal created by new-deal was born with no city at all.';

-- ---------- backfill from the passthrough ----------
do $$
declare moved int;
begin
  update deal d
     set city = nullif(btrim(d.source_row->>'city'), ''),
         updated_by = (select id from actor where slug = 'system')
   where coalesce(btrim(d.source_row->>'city'), '') <> ''
     and d.city is distinct from nullif(btrim(d.source_row->>'city'), '');
  get diagnostics moved = row_count;
  raise notice 'deal.city backfilled: % row(s)', moved;

  -- Stop rule made mechanical, same shape as 0017's deal_type backfill: on a
  -- FIRST run, moving nothing means the source_row key changed under us. A
  -- re-run legitimately moves zero, so only fail when the column is still
  -- entirely empty afterwards.
  if moved = 0 and not exists (select 1 from deal where city is not null) then
    raise exception 'city backfill moved ZERO deals and the column is empty — source_row key changed? stop, do not force';
  end if;
end $$;

-- ---------- expose both on the export view ----------
-- Appended at the END of the select list on purpose: `create or replace view`
-- only permits adding columns after the existing ones, and replacing in place
-- keeps the existing grants rather than dropping and re-granting.
create or replace view v_export_deals as
select d.id, d.name, d.salesforce_id, d.deal_type, ph.label as phase, d.segment,
       d.outcome, d.closed_on, d.notes_path,
       c.roster_ref as client_ref, pc.name as client_name,
       initcap(lead_actor.slug) as owner,
       d.sf_commission_placeholder as "PLACEHOLDER_sf_commission_never_sum",
       d.sf_close_date_placeholder as "PLACEHOLDER_sf_close_date_never_forecast",
       d.source_row,
       d.city,
       d.lane
from deal d
join client c on c.id = d.client_id
join party pc on pc.id = c.party_id
join deal_phase ph on ph.slug = d.phase
left join deal_participant dp on dp.deal_id=d.id and dp.role='lead' and dp.to_at is null
left join actor lead_actor on lead_actor.id = dp.actor_id;

grant select on v_export_deals to carr_reader;

commit;
