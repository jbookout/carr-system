-- 0293_lead_board_read_model.sql
--
-- The authenticated DoctorCRE Lead Board needs one safe, complete read door.
-- v_lead_hot ranks only unsuppressed rows and omits the row version, which
-- makes it useful for triage but unsafe as the source for update-lead. These
-- views deliberately expose no contact data, free-form notes, raw source
-- detail, or party address. Suppression is a displayed state, not a filter.

begin;

insert into lead_stage (slug, label, sort) values
  ('new',             'New',              10),
  ('qualified',       'Qualified',        20),
  ('outreach_active', 'Outreach Active',  30),
  ('engaged',         'Engaged',          40),
  ('nurture_drip',    'Nurture (Drip)',   50),
  ('opportunity',     'Opportunity',      60),
  ('active_deal',     'Active Deal',      70),
  ('closed_won',      'Closed-Won',       80),
  ('closed_lost',     'Closed-Lost',      90),
  ('do_not_contact',  'Do Not Contact',  100)
on conflict (slug) do update
set label = excluded.label,
    sort = excluded.sort;

-- do_not_contact is a standing instruction, not merely a board column. Stop
-- every writer at the record boundary if it attempts to create the impossible
-- state the browser also refuses. NOT VALID lets PostgreSQL install the guard
-- before checking history; VALIDATE then fails this migration visibly if old
-- data needs an explicit human-reviewed correction rather than rewriting it.
alter table lead
  add constraint lead_do_not_contact_suppressed
  check (stage <> 'do_not_contact' or suppressed) not valid;

alter table lead validate constraint lead_do_not_contact_suppressed;

create or replace view v_lead_board_stage as
select slug, label, sort
  from lead_stage;

comment on view v_lead_board_stage is
  'The full ordered lead funnel for the authenticated DoctorCRE Lead Board, including empty stages.';

create or replace view v_lead_board as
select l.id,
       l.registry_ref,
       p.name,
       p.specialty,
       p.city,
       p.county,
       p.state,
       l.lane,
       l.stage,
       ls.label as stage_label,
       ls.sort as stage_sort,
       l.score,
       l.segment,
       l.suppressed,
       l.est_lease_event,
       l.event_confidence,
       coalesce(lt.last_touch, l.last_touch) as last_touch,
       l.next_action_date,
       owner.slug as owner,
       coalesce(l.owner_label, initcap(owner.slug)) as owner_label,
       l.version as base_version,
       l.created_at,
       l.updated_at
  from lead l
  join party p on p.id = l.party_id
  join lead_stage ls on ls.slug = l.stage
  left join actor owner on owner.id = l.owner_id
  left join v_last_touch lt
    on lt.subject_type = 'lead' and lt.subject_id = l.id;

comment on view v_lead_board is
  'Every worked lead, including suppressed and terminal rows, with safe display fields and the authoritative base_version required by update-lead. No contact data, notes, addresses, or raw source detail.';

grant select on v_lead_board, v_lead_board_stage to carr_reader;

do $$
declare
  stage_count integer;
begin
  select count(*) into stage_count from v_lead_board_stage;
  if stage_count < 10 then
    raise exception 'Lead Board stage view has only % stages; expected at least 10', stage_count;
  end if;
end $$;

commit;
