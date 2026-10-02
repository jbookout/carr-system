-- W15: explicit invoice dates and the existing commission ledger, no provider effect.
alter table commission add column due_on date;
comment on column commission.due_on is 'Contractual invoice due date; null means unknown. Never inferred from deal close or a payment promise.';

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
       pa.slug as parked_by,
       d.invoiced_on, d.closed_on, d.lane, d.outcome
  from deal d
  join client c on c.id = d.client_id
  join party cp on cp.id = c.party_id
  left join v_client_account vca on vca.client_id = c.id and vca.is_sub_client
  left join national_account_owner nao on nao.account_client_id = vca.account_client_id
  left join actor ao on ao.id = nao.owner_actor_id
  left join deal_market_assignment dma on dma.deal_id = d.id
  left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id
  left join actor pa on pa.id = d.parked_by
 where d.invoiced_on is null;

create or replace view v_deal_reconciliation_read as
select d.id, d.name, d.salesforce_id, d.version as base_version,
       d.phase, d.outcome, d.closed_on, d.invoiced_on, d.lane from deal d;

create view v_invoice_tracker as
select d.id as deal_id, d.name, d.owner, d.phase, d.lane, d.outcome, d.closed_on,
       d.invoiced_on as deal_invoiced_on,
       cm.id as commission_id, cm.gross_amount, cm.status, cm.version as base_version,
       cm.invoiced_on as commission_invoiced_on, cm.received_on, cm.due_on
  from deal d
  join client cl on cl.id=d.client_id and cl.merged_into is null
  join party p on p.id=cl.party_id and p.deleted_at is null and p.merged_into is null
  left join commission cm on cm.deal_id=d.id
 where d.invoiced_on is not null
    or (d.phase='closed' and (d.outcome is null or d.outcome='won'))
    or cm.status in ('invoiced','received');
grant select on v_invoice_tracker to carr_reader, carr_writer;
