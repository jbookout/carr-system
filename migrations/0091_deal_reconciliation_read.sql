-- 0091_deal_reconciliation_read.sql — the narrow, all-deal read required by
-- Salesforce reconciliation.  The home board stays open-only and deliberately
-- does not carry either field; this view is for one resolved deal at a time.

begin;

create view v_deal_reconciliation_read as
select d.id, d.name, d.salesforce_id, d.version as base_version,
       d.phase, d.outcome, d.closed_on
  from deal d;

-- The MCP read path connects as carr_reader.  This view exposes neither the
-- Salesforce placeholder columns nor source_row, so reconciliation can carry
-- its stable external key and optimistic-lock token without widening data.
grant select on v_deal_reconciliation_read to carr_reader;

commit;
