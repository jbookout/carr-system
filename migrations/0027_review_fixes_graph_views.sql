-- 0027: fixes from the ORDER 34 adversarial review (maker≠checker pass run
-- before the graph verbs deployed — findings 3, 5, 7 of the review; the code
-- fixes for blockers 1-2 and the rest live in tools.js, same commit).
--
-- (3) v_ref_index gains party_id (appended LAST — create or replace requires
--     the existing column prefix unchanged). A UUID key, not a contact detail;
--     the safe-columns boundary is about PII and party_id carries none. Lets
--     counterparty-history resolve a ref to an exact party instead of falling
--     back to name matching, which silently welds duplicate-name humans.
-- (5) v_counterparty_history leg A gains the DISTINCT leg B already had —
--     duplicate deal_participant rows must not double-count a deal in the
--     "how many times have we faced them" answer.
-- (7) v_source_attribution's no-linkage sentinel becomes '__unattributed__' —
--     prospect_pool.source is free text, so a plain-word sentinel could
--     collide with a real future lane name and silently blend populations.

begin;

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
       (p.merged_into is not null), null::text, p.id
  from vendor v
  join party p on p.id = v.party_id
  left join party org on org.id = p.org_id
union all
select 'deal', d.id, null::text, d.name, null::text, null::text, null::text, d.phase,
       false, c.roster_ref, null::uuid
  from deal d
  left join client c on c.id = d.client_id;

create or replace view v_counterparty_history as
select distinct
  p.id            as party_id,
  p.name          as party_name,
  p.kind          as party_kind,
  p.city          as party_city,
  p.state         as party_state,
  dp.role         as relationship,
  null::text      as building_address,
  null::text      as building_city,
  null::text      as building_state,
  d.id            as deal_id,
  d.name          as deal_name,
  d.deal_type     as deal_type,
  d.phase         as phase,
  d.outcome       as outcome,
  d.closed_on     as closed_on,
  c.roster_ref    as client_ref
from deal_participant dp
join party  p on p.id = dp.party_id
join deal   d on d.id = dp.deal_id
join client c on c.id = d.client_id
where dp.role in ('listing_side', 'referring_agent')
  and p.merged_into is null
union all
select distinct
  p.id, p.name, p.kind, p.city, p.state,
  bo.kind         as relationship,
  b.address, b.city, b.state,
  d.id, d.name, d.deal_type, d.phase, d.outcome, d.closed_on,
  c.roster_ref
from building_ownership bo
join party    p  on p.id = bo.party_id
join building b  on b.id = bo.building_id
left join space          s  on s.building_id = b.id
left join premises_space ps on ps.space_id   = s.id
left join premises       pr on pr.id         = ps.premises_id
left join deal           d  on d.id          = pr.deal_id
left join client         c  on c.id          = d.client_id
where p.merged_into is null
  and bo.to_on is null;

create or replace view v_source_attribution as
with lead_lane as (
  select l.id         as lead_id,
         l.client_id  as client_id,
         l.created_at as created_at,
         coalesce(pp.source, 'direct:' || l.lane, 'direct:unknown') as lane
  from lead l
  left join lateral (
    select source from prospect_pool
    where promoted_lead_id = l.id
    limit 1
  ) pp on true
),
deal_attrib as (
  select d.id        as deal_id,
         d.outcome   as outcome,
         d.won_value as won_value,
         coalesce((select ll.lane from lead_lane ll
                    where ll.client_id = d.client_id
                    order by ll.created_at, ll.lead_id
                    limit 1), '__unattributed__') as lane
  from deal d
),
commission_by_deal as (
  select deal_id,
         sum(gross_amount) filter (where status = 'received')                as received,
         sum(gross_amount) filter (where status in ('expected', 'invoiced')) as open
  from commission
  group by deal_id
),
pool_stage as (
  select source as lane,
         count(*)                                   as pool_rows,
         count(*) filter (where status = 'promoted') as promoted
  from prospect_pool
  group by source
),
lane_leads as (
  select lane,
         count(*)                  as leads_total,
         count(distinct client_id) as clients_converted
  from lead_lane
  group by lane
),
lane_deals as (
  select da.lane,
         count(*)                                    as deals,
         count(*) filter (where da.outcome = 'won')  as deals_won,
         sum(da.won_value) filter (where da.outcome = 'won') as won_value_total,
         sum(cb.received)                            as commission_received,
         sum(cb.open)                                as commission_open
  from deal_attrib da
  left join commission_by_deal cb on cb.deal_id = da.deal_id
  group by da.lane
)
select
  coalesce(ps.lane, ll.lane, ld.lane) as lane,
  coalesce(ps.pool_rows, 0)           as pool_rows,
  coalesce(ps.promoted, 0)            as promoted,
  coalesce(ll.leads_total, 0)         as leads_total,
  coalesce(ll.clients_converted, 0)   as clients_converted,
  coalesce(ld.deals, 0)               as deals,
  coalesce(ld.deals_won, 0)           as deals_won,
  ld.won_value_total                  as won_value_total,
  ld.commission_received              as commission_received,
  ld.commission_open                  as commission_open
from pool_stage ps
full outer join lane_leads ll on ll.lane = ps.lane
full outer join lane_deals ld on ld.lane = coalesce(ps.lane, ll.lane);

comment on view v_source_attribution is
  '0026+0027 / ORDER 33: per-lane funnel. One lane per deal (earliest linked '
  'lead); deals with no lead linkage appear as lane=__unattributed__ (dunder '
  'sentinel — cannot collide with a real pool source name) so totals reconcile '
  'against the whole book. Money columns read the commission table ONLY.';

commit;
