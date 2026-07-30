-- ============================================================================
-- 0004: ROLES + VIEWS (enforcement by construction, schema §8)
-- carr_reader / carr_writer are NOLOGIN privilege bundles; login roles are
-- created via neonctl and GRANTed a bundle, so no password ever appears in
-- SQL or git. Views run with definer semantics (Postgres default), so
-- carr_reader needs — and gets — ZERO base-table grants.
-- Views are v1: correct against the empty schema, iterated by further
-- migrations as the exporters are verified against real data.
-- ============================================================================

-- ---------- privilege bundles ----------
create role carr_writer nologin;
create role carr_reader nologin;

grant usage on schema public to carr_writer, carr_reader;
grant select, insert, update on all tables in schema public to carr_writer;
grant usage, select on all sequences in schema public to carr_writer;
-- no DELETE anywhere: purge paths are scrub-in-place by design (A9).
-- carr_reader gets view grants only, at the bottom of this file.

-- ---------- helper views ----------

-- one normalized rate per availability row, gap visible
create view v_rate_normalized as
select a.id, a.space_id, a.observed_at, a.source, a.status,
       a.rate_amount, a.rate_basis,
       coalesce(a.rate_norm_sf_yr, a.rate_norm_gross_sf_yr) as rate_sf_yr,
       a.norm_owed, a.opex_sf_yr, a.available_on, a.note
from availability a;

-- last business touch per subject, derived (never hand-stamped)
create view v_last_touch as
select 'deal' as subject_type, deal_id as subject_id, max(occurred_at)::date as last_touch
  from activity where deal_id is not null group by deal_id
union all
select 'client', client_id, max(occurred_at)::date from activity where client_id is not null group by client_id
union all
select 'lead', lead_id, max(occurred_at)::date from activity where lead_id is not null group by lead_id
union all
select 'vendor', vendor_id, max(occurred_at)::date from activity where vendor_id is not null group by vendor_id;

-- the catch-me-up substrate: one merged timeline per subject
create view v_subject_timeline as
select 'activity' as entry_kind, a.id, a.occurred_at, a.recorded_at, act.slug as actor,
       a.kind as verb, a.summary, a.detail, a.owed,
       coalesce(a.deal_id, a.client_id, a.lead_id, a.vendor_id) as subject_id,
       case when a.deal_id is not null then 'deal'
            when a.client_id is not null then 'client'
            when a.lead_id is not null then 'lead'
            else 'vendor' end as subject_type
from activity a join actor act on act.id = a.actor_id
union all
select 'event', e.id, e.occurred_at, e.recorded_at, act.slug,
       e.verb, coalesce(e.field, e.verb), e.human_quote, null,
       e.subject_id, e.subject_type
from event e join actor act on act.id = e.actor_id;

-- ---------- operating views ----------

create view v_deal_board as
select d.id, d.name, c.roster_ref as client_ref, pc.name as client_name,
       d.deal_type, d.phase, ph.sort as phase_sort, d.segment, d.outcome,
       lead_actor.slug as lead_owner, lt.last_touch,
       d.notes_path
from deal d
join client c on c.id = d.client_id
join party pc on pc.id = c.party_id
join deal_phase ph on ph.slug = d.phase
left join deal_participant dp on dp.deal_id = d.id and dp.role = 'lead' and dp.to_at is null
left join actor lead_actor on lead_actor.id = dp.actor_id
left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id;
-- placeholder rule: sf_commission_placeholder / sf_close_date_placeholder are
-- deliberately ABSENT here; boards group by phase/segment only.

create view v_today_triage as
select 'next_action' as item_kind, na.id, na.subject_type, na.subject_id,
       owner.slug as owner, na.description as what, na.due_on
from next_action na join actor owner on owner.id = na.owner_id
where na.status = 'open' and (na.due_on is null or na.due_on <= current_date)
union all
select 'critical_date', cd.id, 'deal', cd.deal_id, null,
       cd.kind || coalesce(': ' || cd.note, ''), cd.due_on
from critical_date cd
where cd.status = 'open' and cd.due_on <= current_date + 14
union all
select 'ingest', i.id, 'inbox', i.id, null, i.source || ' item awaiting triage', i.received_at::date
from ingest_inbox i where i.status = 'new';

create view v_lead_hot as
select l.id, l.registry_ref, p.name, p.specialty, p.city, p.county, p.state,
       l.lane, l.stage, l.score, l.segment, l.suppressed,
       l.est_lease_event, l.event_confidence, lt.last_touch, l.next_action_date
from lead l
join party p on p.id = l.party_id
left join v_last_touch lt on lt.subject_type = 'lead' and lt.subject_id = l.id
where not l.suppressed;
-- NEVER pre-filtered beyond suppression: all leads surface; Joe qualifies.

create view v_stale_records as
select 'deal' as subject_type, d.id, d.name, lt.last_touch,
       current_date - lt.last_touch as days_quiet
from deal d left join v_last_touch lt on lt.subject_type='deal' and lt.subject_id=d.id
where d.outcome is null and d.phase not in ('closed')
  and (lt.last_touch is null or lt.last_touch < current_date - 14);

create view v_integrity_digest as
select 'row_counts' as line, jsonb_build_object(
         'deals', (select count(*) from deal),
         'clients', (select count(*) from client),
         'leads', (select count(*) from lead),
         'vendors', (select count(*) from vendor),
         'activities_7d', (select count(*) from activity where recorded_at > now() - interval '7 days'),
         'events_24h', (select count(*) from event where recorded_at > now() - interval '24 hours')) as value
union all
select 'writes_by_dell_24h', to_jsonb((select count(*) from event e join actor a on a.id=e.actor_id
         where a.slug='dell' and e.recorded_at > now() - interval '24 hours'))
union all
select 'export_freshness', coalesce((select jsonb_object_agg(t.target,
         jsonb_build_object('last_ok', t.last_ok, 'stale', t.last_ok < now() - interval '26 hours'))
       from (select target, max(ran_at) filter (where status='ok') as last_ok
             from export_run group by target) t), '{}'::jsonb)
union all
select 'norm_owed_open', to_jsonb((select count(*) from availability where norm_owed))
union all
select 'merge_queue', to_jsonb((select count(*) from ingest_inbox where status='new'));

-- ---------- export views (A8; labeled passthrough where noted) ----------

create view v_export_leads as
select l.registry_ref            as "Lead ID",
       l.created_at::date        as "Date In",
       owner.slug                as "Owner",
       l.stage                   as "Stage",
       l.segment                 as "Segment",
       p.name                    as "Contact Name",
       org.name                  as "Practice",
       p.specialty               as "Specialty",
       p.city                    as "City/Market",
       p.county                  as "County",
       p.email                   as "Email",
       p.phone                   as "Phone",
       l.source_type             as "Source Type",
       l.source_detail           as "Source Detail (V-ID / event / referrer)",
       l.report_back_due         as "Report-Back Due",
       l.drip_campaign           as "Drip Campaign",
       l.drip_added              as "Drip Added",
       na.description            as "Next Action",
       na.due_on                 as "Next Action Date",
       lt.last_touch             as "Last Touch",
       l.sf_deal                 as "SF Deal",
       l.notes_path              as "Detail File",
       l.notes                   as "Notes",
       l.est_lease_event         as "Est-Lease-Event",
       l.event_source            as "Event-Source",
       l.event_confidence        as "Event-Confidence",
       l.suppressed              as "_suppressed"      -- carried, never dropped
from lead l
join party p on p.id = l.party_id
left join party org on org.id = p.org_id
left join actor owner on owner.id = l.created_by
left join next_action na on na.subject_type='lead' and na.subject_id=l.id
       and na.status='open' and na.owner_id = l.created_by
left join v_last_touch lt on lt.subject_type='lead' and lt.subject_id=l.id;
-- NOTE for the exporter build: Owner here maps from created_by as v1; the
-- import must decide whether registry Owner becomes a dedicated lead.owner_id
-- (likely yes — reconcile at import and iterate this view by migration).

create view v_export_clients as
select c.roster_ref              as "Client ID",
       p.name                    as "Name",
       org.name                  as "Practice / Entity",
       owner.slug                as "Owner",
       c.status                  as "Status",
       coalesce(c.vertical,'') || coalesce(' / ' || c.subtype,'') as "Specialty / Type",
       coalesce(p.city,'') || coalesce(', ' || p.state,'')        as "Market / Location",
       (select string_agg(distinct d.deal_type, ', ') from deal d where d.client_id=c.id) as "Deal Type",
       c.acquisition_source      as "Referral Source",
       p.name                    as "Contact",
       p.phone                   as "Phone",
       p.email                   as "Email",
       (select string_agg(rs.source_system || ':' || rs.external_key, '; ')
          from record_source rs where rs.entity_type='client' and rs.entity_id=c.id
           and rs.source_system='dup_candidate')     as "Possible Duplicate Of",
       c.notes_path              as "Detail File"
from client c
join party p on p.id = c.party_id
left join party org on org.id = p.org_id
left join actor owner on owner.id = c.created_by;

create view v_export_clients_active as
select la.slug as "Owner", pc.name as "Name", c.roster_ref as "C-ID",
       c.status as "Status", d.deal_type as "Deal Type",
       coalesce(c.vertical,'') as "Specialty",
       coalesce(pc.city,'') || coalesce(', ' || pc.state,'') as "Location",
       lt.last_touch as "Last Touch",
       na.description as "Next Step",
       c.notes_path as "Detail"
from deal d
join client c on c.id = d.client_id
join party pc on pc.id = c.party_id
left join deal_participant dp on dp.deal_id=d.id and dp.role='lead' and dp.to_at is null
left join actor la on la.id = dp.actor_id
left join v_last_touch lt on lt.subject_type='deal' and lt.subject_id=d.id
left join next_action na on na.subject_type='deal' and na.subject_id=d.id
       and na.status='open' and na.owner_id = dp.actor_id
where d.outcome is null and d.phase <> 'closed';

-- THE one view that exposes the Salesforce placeholders: labeled passthrough,
-- no aggregation, no ordering by them (A8). source_row rides along so the
-- exporter can reproduce unmapped legacy JSON fields verbatim.
create view v_export_deals as
select d.id, d.name, d.salesforce_id, d.deal_type, d.phase, d.segment,
       d.outcome, d.closed_on, d.notes_path,
       c.roster_ref as client_ref, pc.name as client_name,
       lead_actor.slug as owner,
       d.sf_commission_placeholder as "PLACEHOLDER_sf_commission_never_sum",
       d.sf_close_date_placeholder as "PLACEHOLDER_sf_close_date_never_forecast",
       d.source_row
from deal d
join client c on c.id = d.client_id
join party pc on pc.id = c.party_id
left join deal_participant dp on dp.deal_id=d.id and dp.role='lead' and dp.to_at is null
left join actor lead_actor on lead_actor.id = dp.actor_id;

create view v_export_vendors as
select v.vendor_ref              as "ID",
       p.name                    as "Name",
       org.name                  as "Company",
       v.category                as "Category",
       array_to_string(v.verticals, ', ') as "Vertical",
       null::text                as "Title",          -- import decides: party attr (reconcile)
       owner.slug                as "Owner",
       v.stage                   as "Stage",
       lt.last_touch             as "Last Touch",
       na.description            as "Next Step",
       v.referral_active         as "Referral-active?",
       v.territory               as "Territory",
       p.state                   as "State",
       v.offers                  as "Offers",
       v.seeking                 as "Seeking",
       (select string_agg(pl.kind || '→' || p2.name, '; ')
          from party_link pl join party p2 on p2.id = pl.to_party
         where pl.from_party = v.party_id)            as "Links",
       v.rivalry_group           as "Rivalry Group",
       v.originated              as "Originated / Referred",
       p.phone                   as "Phone",
       p.email                   as "Email",
       v.intro_notes             as "Notes",
       v.enrich                  as "Enrich?",
       v.out_of_market           as "_out_of_market"  -- sheet router, not a column
from vendor v
join party p on p.id = v.party_id
left join party org on org.id = p.org_id
left join actor owner on owner.id = v.owner_id
left join next_action na on na.subject_type='vendor' and na.subject_id=v.id and na.status='open'
left join v_last_touch lt on lt.subject_type='vendor' and lt.subject_id=v.id;

-- ---------- reader grants: views ONLY ----------
grant select on
  v_rate_normalized, v_last_touch, v_subject_timeline,
  v_deal_board, v_today_triage, v_lead_hot, v_stale_records, v_integrity_digest,
  v_export_leads, v_export_clients, v_export_clients_active, v_export_deals,
  v_export_vendors
to carr_reader;
