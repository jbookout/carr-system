-- order29b-death-sentences.sql — ORDER 29b(b): the lane JSONs and the router xlsx
-- get their retirement dates. Ratified by Joe 2026-08-04, gate opened 2026-08-05
-- (decision 8e4f7669). DELETES NOTHING: a date in safe_to_drop_after is a debt
-- with a payoff day; the drop itself stays a separate, evidenced act after
-- `run.sh health` shows zero executable references (parity before any file dies,
-- no consumer flip and file retirement in the same order).
--
-- 2026-08-19 gives every weekly lane at least one real run on the records path
-- after the 29b repoints, past the shadow week, before anything is removed.

begin;

update deprecation
   set safe_to_drop_after = date '2026-08-19'
 where object_kind = 'file'
   and dropped_at is null
   and safe_to_drop_after is null
   and object_name in (
     'Automation/entity-formation-leads.json',
     'Automation/pre-entity-watch.json',
     'Automation/relocating-owner-leads.json',
     'Automation/renewal-radar.json',
     'DNA/Team/national-accounts.json',
     'DNA/Leads/lead-router-2026-07-13.xlsx');

-- guard: exactly the six named rows carry the sentence, none was already dropped
do $$
declare sentenced int;
begin
  select count(*) into sentenced from deprecation
   where object_kind = 'file' and safe_to_drop_after = date '2026-08-19'
     and dropped_at is null;
  if sentenced <> 6 then
    raise exception 'expected 6 sentenced file rows, found % — rolled back', sentenced;
  end if;
end $$;

commit;
