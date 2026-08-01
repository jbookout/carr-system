-- 0026: the two graph views of Joe's 7/31 mandate (Fable seat session 4).
--
-- (a) v_counterparty_history — ORDER 27 EXTENSION (d). "We're negotiating
--     against X — where have we faced X, what happened?" Two legs: deal-level
--     counterparty roles (deal_participant listing_side/referring_agent) and
--     building-level roles (building_ownership), walked to deals through
--     premises when the linkage exists. SAFE COLUMNS ONLY — no phone, no
--     email, no notes_path; the column list is a security boundary
--     (v_ref_index precedent). [D5] binds doubly: INTERNAL-seat only, never
--     an export target, never client-facing.
--
-- (b) v_source_attribution — ORDER 33 (a). Which lane has ever produced a
--     commission: prospect_pool.promoted_lead_id -> lead -> lead.client_id
--     -> client -> deal -> commission, rolled up per lane. Each DEAL
--     attributes to exactly ONE lane (the earliest linked lead's lane,
--     deterministic tiebreak) so the rows reconcile against the whole book;
--     deals whose client has no lead linkage land in 'unattributed' rather
--     than vanishing (the absence-in-a-partial-collection lesson, made
--     structural). sf_commission_placeholder NEVER appears here (0001 rule);
--     money columns read the commission table alone.

begin;

create view v_counterparty_history as
select
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

comment on view v_counterparty_history is
  '0026 / ORDER 27 EXT (d): counterparty relationships (listing agents, landlords, '
  'owners, property managers, referring agents) walked to deals. SAFE COLUMNS ONLY '
  '— the column list is a security boundary; adding phone/email/notes is a design '
  'call, not an edit. [D5]: internal-seat only, NEVER an export target or '
  'client-facing surface.';

create view v_source_attribution as
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
  -- every deal to exactly ONE lane: earliest linked lead wins (deterministic)
  select d.id        as deal_id,
         d.outcome   as outcome,
         d.won_value as won_value,
         coalesce((select ll.lane from lead_lane ll
                    where ll.client_id = d.client_id
                    order by ll.created_at, ll.lead_id
                    limit 1), 'unattributed') as lane
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
  '0026 / ORDER 33 (a): per-lane funnel pool->promoted->leads->clients->deals->'
  'commissions. One lane per deal (earliest linked lead); deals with no lead '
  'linkage appear as lane=unattributed so totals reconcile against the whole '
  'book. Money columns read the commission table ONLY — '
  'sf_commission_placeholder is banned here by 0001''s standing rule.';

grant select on v_counterparty_history, v_source_attribution to carr_reader;
-- carr_writer gets them too: write verbs resolve/validate through views and the
-- 0020 incident (v_ref_index missing from writer) is a class we don't repeat.
grant select on v_counterparty_history, v_source_attribution to carr_writer;

commit;
