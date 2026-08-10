-- 0090_deal_room_workspaces.sql — national-account workspaces, market-agent
-- assignments, and review agendas for the Deal Room. The deal stays at the
-- franchisee/client grain established by 0061; this layer never duplicates or
-- reparents a deal.

begin;

create table national_account_owner (
  account_client_id uuid primary key references client(id),
  owner_actor_id    uuid not null references actor(id),
  set_by            uuid not null references actor(id),
  set_at            timestamptz not null default now()
);

comment on table national_account_owner is
  'The partner accountable for a national-account portfolio. This is not the '
  'owner of every market deal; individual deals keep their own owner.';

create table deal_market_assignment (
  deal_id       uuid primary key references deal(id),
  agent_name    text not null check (btrim(agent_name) <> ''),
  agent_party_id uuid references party(id),
  market        text,
  source        text not null,
  set_by        uuid not null references actor(id),
  set_at        timestamptz not null default now()
);

comment on table deal_market_assignment is
  'The local CARR agent assigned to a national-account transaction. agent_name '
  'is the stated agenda label; agent_party_id is filled only when identity is verified.';

create table deal_review_session (
  id                uuid primary key default gen_random_uuid(),
  workspace_kind    text not null check (workspace_kind in ('team','national_account')),
  account_client_id uuid references client(id),
  started_by        uuid not null references actor(id),
  started_at        timestamptz not null default now(),
  ended_at          timestamptz,
  status            text not null default 'open' check (status in ('open','completed','abandoned')),
  summary           text,
  check ((workspace_kind = 'team' and account_client_id is null)
      or (workspace_kind = 'national_account' and account_client_id is not null)),
  check ((status = 'open' and ended_at is null)
      or (status <> 'open' and ended_at is not null))
);

create unique index deal_review_one_open_per_actor_workspace
  on deal_review_session (started_by, workspace_kind, coalesce(account_client_id, '00000000-0000-0000-0000-000000000000'::uuid))
  where status = 'open';

create table deal_review_item (
  session_id  uuid not null references deal_review_session(id),
  deal_id     uuid not null references deal(id),
  disposition text not null check (disposition in ('reviewed','skipped')),
  note        text,
  reviewed_at timestamptz not null default now(),
  primary key (session_id, deal_id)
);

create index deal_review_item_deal_idx on deal_review_item (deal_id, reviewed_at desc);

-- Joe's explicit ruling in the build conversation: Musicologie is Dell's
-- national account. The parent row was created by 0061 and is the only safe
-- anchor; no deal-name or segment inference is used here.
insert into national_account_owner (account_client_id, owner_actor_id, set_by)
select c.id, a.id, a.id
  from client c
  join party p on p.id = c.party_id
  join actor a on a.slug = 'dell'
 where c.client_type = 'national_account'
   and lower(p.name) = 'musicologie'
on conflict (account_client_id) do nothing;

create view v_deal_room_account as
select c.id as account_client_id,
       c.roster_ref as account_client_ref,
       p.name as account_name,
       a.slug as account_owner,
       count(d.id) filter (where d.outcome is null) as open_deals,
       count(d.id) filter (where d.outcome is null and d.attention) as attention_deals,
       count(d.id) filter (where d.outcome is null and d.next_date < current_date) as overdue_deals,
       count(d.id) filter (where d.outcome is null and
         (lt.last_touch is null or lt.last_touch < current_date - 14)) as stale_deals,
       (select max(rs.ended_at) from deal_review_session rs
         where rs.account_client_id = c.id and rs.status = 'completed') as last_review_at
  from client c
  join party p on p.id = c.party_id
  left join national_account_owner nao on nao.account_client_id = c.id
  left join actor a on a.id = nao.owner_actor_id
  left join v_client_account vca on vca.account_client_id = c.id and vca.is_sub_client
  left join deal d on d.client_id = vca.client_id
  left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id
 where c.client_type = 'national_account' and c.merged_into is null
 group by c.id, c.roster_ref, p.name, a.slug;

create view v_deal_room_session as
select s.id as session_id, s.workspace_kind, s.account_client_id,
       a.slug as started_by, s.started_at, s.ended_at, s.status, s.summary,
       count(i.deal_id) filter (where i.disposition = 'reviewed') as reviewed_count,
       count(i.deal_id) filter (where i.disposition = 'skipped') as skipped_count
  from deal_review_session s
  join actor a on a.id = s.started_by
  left join deal_review_item i on i.session_id = s.id
 group by s.id, a.slug;

create view v_deal_room_action as
select n.id, n.subject_id as deal_id, a.slug as owner, n.description,
       n.due_on, n.status, n.updated_at
  from next_action n join actor a on a.id=n.owner_id
 where n.subject_type='deal';

create view v_deal_room_activity as
select x.id, x.deal_id, x.occurred_at, a.slug as actor, x.kind,
       x.summary, x.detail, x.source
  from activity x join actor a on a.id=x.actor_id
 where x.deal_id is not null;

create view v_deal_room_participant as
select dp.id, dp.deal_id, dp.role, coalesce(a.display_name,p.name) as name,
       a.slug as actor, p.id as party_id
  from deal_participant dp
  left join actor a on a.id=dp.actor_id
  left join party p on p.id=dp.party_id
 where dp.to_at is null;

create view v_deal_room_premises as
select pr.id, pr.deal_id, pr.label, b.name as building_name, b.address,
       b.city, b.state, s.suite, s.area_amount, s.area_basis, pr.created_at
  from premises pr
  left join premises_space ps on ps.premises_id=pr.id
  left join space s on s.id=ps.space_id
  left join building b on b.id=s.building_id;

create view v_deal_room_negotiation as
select id, deal_id, round_no, side, proposed_on, rate_amount, rate_basis,
       rate_norm_sf_yr, ti_amount, ti_basis, free_rent_months, term_months,
       escalator, opex_note, expires_on, note, source
  from negotiation_round;

create view v_deal_room_document as
select id, deal_id, sent_status, lint_passed, leak_check_passed,
       prepared_at, note
  from document where deal_id is not null;

-- Preserve the original nine columns in order so CREATE OR REPLACE is safe;
-- append the workspace/intelligence fields after them.
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
       case when vca.account_client_id is null then 'team' else 'national_account' end as workspace_kind
  from deal d
  join client c on c.id = d.client_id
  join party cp on cp.id = c.party_id
  left join v_client_account vca on vca.client_id = c.id and vca.is_sub_client
  left join national_account_owner nao on nao.account_client_id = vca.account_client_id
  left join actor ao on ao.id = nao.owner_actor_id
  left join deal_market_assignment dma on dma.deal_id = d.id
  left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id
 where d.outcome is null;

grant select, insert, update on national_account_owner, deal_market_assignment,
  deal_review_session, deal_review_item to carr_writer;
grant select on v_deal_room_account, v_deal_room_session, v_deal_room_board,
  v_deal_room_action, v_deal_room_activity, v_deal_room_participant,
  v_deal_room_premises, v_deal_room_negotiation, v_deal_room_document to carr_reader;
grant select on v_deal_room_account, v_deal_room_session, v_deal_room_board,
  v_deal_room_action, v_deal_room_activity, v_deal_room_participant,
  v_deal_room_premises, v_deal_room_negotiation, v_deal_room_document to carr_writer;

commit;
