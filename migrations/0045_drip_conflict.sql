-- 0045_drip_conflict.sql — four clients in active deals were queued on a PROSPECTING drip.
--
-- Surfaced by `run.sh graph-health` as its only HIGH finding:
--   "LIVE CLIENT ON A PROSPECTING DRIP — Jonathan Tubbs — C-023 is 'Active deal –
--    Negotiation' but lead L-150 is 'Nurture (Drip)' on 'Monthly Newsletter'."
--
-- It was not one record. Four leads carry drip_campaign='Monthly Newsletter', suppressed
-- = false, and a linked client whose status is active_deal:
--   L-093 Collin Myrick · L-132 Edwin Witcher · L-139 Tyler Gregory · L-150 Jonathan Tubbs
--
-- NOTHING HAS BEEN MAILED, and saying otherwise would overstate it. Loop #47 is "Monthly
-- newsletter drip — FIRST SEND", still open: the campaign has never fired. This is a
-- LATENT fault. The day that newsletter launches, four people in live negotiations get a
-- prospecting email from the agent representing them — the kind of thing a client
-- forwards to their attorney, not the kind anyone catches in a send log afterwards.
--
-- WHAT MAKES IT WORTH A MIGRATION RATHER THAN FOUR EDITS: every fact needed to prevent it
-- was already in the database. The lead is LINKED to the client (client_id is set, not
-- inferred), the client status says active_deal, the deal board says negotiation. Three
-- independent signals, and nothing compared them. Same shape as every other finding in
-- this audit: the data was right and no mechanism read it.
--
-- THE FIX IS THE STAGE, NOT A FLAG. lead_stage already carries 'active_deal' as a value,
-- so a lead whose client is in a live deal simply is not in nurture — it was mislabelled.
-- Setting the stage truthfully removes it from the drip by description rather than by
-- suppression, which is why drip_campaign is cleared rather than `suppressed` being set:
-- suppression hides a row from lead-hot, a different question about board visibility that
-- is Joe's to answer, not a side effect this migration should assume.
--
-- NOT TOUCHED: L-040 Nikki Cottis, whose campaign is 'Client Care (post-close)'. That is
-- the correct campaign for a client and exactly what graph-health recommends ("Move to
-- 'Client Care (post-close)', do not just remove them"). She matched a neighbouring query
-- and is not a fault.

begin;

update lead l
   set stage = 'active_deal',
       drip_campaign = null
  from client c
 where c.id = l.client_id
   and l.drip_campaign = 'Monthly Newsletter'
   and l.suppressed = false
   and c.status = 'active_deal';

-- ── the detector, so this cannot go unnoticed again ──
-- A prospecting campaign is any drip that is not the post-close client-care track. If a
-- lead sits on one while its linked client is live, that is a conflict regardless of which
-- campaign names appear later, so the rule is written as "not client care" rather than as
-- a list of prospecting campaigns that would go stale the moment one is added.
create or replace view v_drip_conflict as
select l.registry_ref,
       p.name,
       l.stage,
       l.drip_campaign,
       c.roster_ref  as client_ref,
       c.status      as client_status
  from lead l
  join party p  on p.id = l.party_id
  join client c on c.id = l.client_id
 where l.drip_campaign is not null
   and l.drip_campaign not ilike '%client care%'
   and l.suppressed = false
   and c.status in ('active_deal', 'client', 'past_client');

comment on view v_drip_conflict is
  'Leads queued on a PROSPECTING drip whose linked client is live. Empty is the correct '
  'state. Added 0045 after four clients in active deals were found on the Monthly '
  'Newsletter — latent, since loop #47 (first send) has never fired, which is exactly why '
  'nothing caught it. "not ilike client care" rather than a list of prospecting campaigns: '
  'a list goes stale the first time someone adds a campaign and forgets this view exists.';

-- guards INSIDE the transaction
do $$
declare remaining int; fixed int; cottis text;
begin
  select count(*) into remaining from v_drip_conflict;
  if remaining > 0 then
    raise exception '% lead(s) still on a prospecting drip with a live client', remaining;
  end if;

  select count(*) into fixed from lead l join client c on c.id = l.client_id
   where l.stage = 'active_deal' and c.status = 'active_deal';
  if fixed < 4 then
    raise exception 'expected at least 4 leads moved to active_deal, found % — the update '
                    'matched fewer rows than the diagnosis found', fixed;
  end if;

  -- Nikki Cottis must keep her client-care campaign: she was never the fault.
  select l.drip_campaign into cottis from lead l where l.registry_ref = 'L-040';
  if cottis is distinct from 'Client Care (post-close)' then
    raise exception 'L-040 (Cottis) lost her client-care campaign; she was correct: %', cottis;
  end if;

  raise notice 'drip conflicts cleared (% leads now active_deal); v_drip_conflict empty', fixed;
end $$;

commit;
