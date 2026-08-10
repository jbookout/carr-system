-- 0092_deal_operating_state.sql — Salesforce record existence is not active work.
--
-- Salesforce sometimes requires a deal-shaped row before there is a real
-- transaction. Other rows represent clients who deliberately paused. Neither
-- belongs in the weekly working pipeline, but neither should be closed,
-- deleted, or stripped of its history. operating_state is therefore a second,
-- reversible axis beside phase/outcome.

begin;

alter table deal add column if not exists operating_state text not null default 'active';
alter table deal add column if not exists parking_reason text;
alter table deal add column if not exists parking_note text;
alter table deal add column if not exists parked_at timestamptz;
alter table deal add column if not exists parked_by uuid references actor(id);

alter table deal drop constraint if exists deal_operating_state_check;
alter table deal add constraint deal_operating_state_check
  check (operating_state in ('active','parked'));

alter table deal drop constraint if exists deal_parking_shape_check;
alter table deal add constraint deal_parking_shape_check check (
  (operating_state = 'active'
    and parking_reason is null and parking_note is null
    and parked_at is null and parked_by is null)
  or
  (operating_state = 'parked'
    and parking_reason in ('prospect_never_active','client_paused','other')
    and parked_at is not null and parked_by is not null)
);

comment on column deal.operating_state is
  'Whether this Salesforce-linked row belongs in active operating work. Parking is reversible and never means closed, lost, or deleted.';
comment on column deal.parking_reason is
  'Why the row is parked now: prospect_never_active, client_paused, or other. Event history preserves prior reasons after reactivation.';

-- The generic Deal Room field-conflict path also protects simultaneous
-- park/unpark decisions.
alter table deal_conflict drop constraint if exists deal_conflict_field_check;
alter table deal_conflict add constraint deal_conflict_field_check
  check (field in ('phase','owner','attention','next_date','operating_state'));

create or replace view v_deal_room_deal as
select d.id, d.phase, d.owner, d.deal_type as type, d.city, d.segment,
       d.attention, d.next_date, d.name, d.operating_state, d.parking_reason,
       d.parking_note, d.parked_at
  from deal d;

-- Preserve the original account columns in order; append parked_deals.
-- Existing "open_deals" consumers now receive active work only.
create or replace view v_deal_room_account as
select c.id as account_client_id,
       c.roster_ref as account_client_ref,
       p.name as account_name,
       a.slug as account_owner,
       count(d.id) filter (where d.outcome is null and d.operating_state = 'active') as open_deals,
       count(d.id) filter (where d.outcome is null and d.operating_state = 'active' and d.attention) as attention_deals,
       count(d.id) filter (where d.outcome is null and d.operating_state = 'active' and d.next_date < current_date) as overdue_deals,
       count(d.id) filter (where d.outcome is null and d.operating_state = 'active' and
         (lt.last_touch is null or lt.last_touch < current_date - 14)) as stale_deals,
       (select max(rs.ended_at) from deal_review_session rs
         where rs.account_client_id = c.id and rs.status = 'completed') as last_review_at,
       count(d.id) filter (where d.outcome is null and d.operating_state = 'parked') as parked_deals
  from client c
  join party p on p.id = c.party_id
  left join national_account_owner nao on nao.account_client_id = c.id
  left join actor a on a.id = nao.owner_actor_id
  left join v_client_account vca on vca.account_client_id = c.id and vca.is_sub_client
  left join deal d on d.client_id = vca.client_id
  left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id
 where c.client_type = 'national_account' and c.merged_into is null
 group by c.id, c.roster_ref, p.name, a.slug;

-- Preserve the original board columns in order; append the operating state.
-- Parked rows remain readable so the Parked filter can restore them.
create or replace view v_deal_room_board as
select d.id, d.name, d.deal_type as type, d.phase, d.owner, d.attention,
       d.next_date,
       coalesce(
         (select n.description from next_action n
           where n.subject_type = 'deal' and n.subject_id = d.id and n.status = 'open'
           order by n.updated_at desc, n.id desc limit 1),
         (select n.text from deal_note n
           where n.deal_id = d.id and n.kind = 'next_step'
           order by n.created_at desc, n.id desc limit 1)
       ) as next_step,
       d.city as market,
       d.segment,
       c.id as client_id,
       c.roster_ref as client_ref,
       cp.name as client_name,
       vca.account_client_id,
       vca.account_client_ref,
       vca.account_name,
       ao.slug as account_owner,
       dma.agent_name as market_agent,
       dma.agent_party_id as market_agent_party_id,
       lt.last_touch,
       (select max(i.reviewed_at) from deal_review_item i
         join deal_review_session s on s.id = i.session_id
        where i.deal_id = d.id and i.disposition = 'reviewed' and s.status = 'completed') as last_review_at,
       case when vca.account_client_id is null then 'team' else 'national_account' end as workspace_kind,
       d.operating_state,
       d.parking_reason,
       d.parking_note,
       d.parked_at,
       pa.slug as parked_by
  from deal d
  join client c on c.id = d.client_id
  join party cp on cp.id = c.party_id
  left join v_client_account vca on vca.client_id = c.id and vca.is_sub_client
  left join national_account_owner nao on nao.account_client_id = vca.account_client_id
  left join actor ao on ao.id = nao.owner_actor_id
  left join deal_market_assignment dma on dma.deal_id = d.id
  left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id
  left join actor pa on pa.id = d.parked_by
 where d.outcome is null;

grant select on v_deal_room_deal, v_deal_room_account, v_deal_room_board to carr_reader;
grant select on v_deal_room_deal, v_deal_room_account, v_deal_room_board to carr_writer;

commit;
