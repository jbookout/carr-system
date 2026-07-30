-- 0009: fidelity fixes from the first reconciliation diff (2,178 cell diffs
-- classified into six classes; this migration + importer/exporter edits
-- drive them toward zero).
alter table party  add column title text;                    -- vendors' Title column
alter table vendor add column owner_label text;              -- faithful 'Joe'/'Shared'
alter table vendor alter column enrich drop not null;        -- tri-state: blank is not No
alter table vendor alter column enrich drop default;
alter table lead   add column est_lease_event_raw text;      -- 'M2M' etc, verbatim
alter table client add column contact_label text;            -- roster Contact ≠ Name

drop view v_export_leads;
create view v_export_leads as
select l.registry_ref            as "Lead ID",
       l.created_at::date        as "Date In",
       coalesce(l.owner_label, owner.display_name) as "Owner",
       ls.label                  as "Stage",
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
       coalesce(l.est_lease_event::text, l.est_lease_event_raw) as "Est-Lease-Event",
       l.event_source            as "Event-Source",
       l.event_confidence        as "Event-Confidence",
       l.suppressed              as "_suppressed"
from lead l
join party p on p.id = l.party_id
join lead_stage ls on ls.slug = l.stage
left join party org on org.id = p.org_id
left join actor owner on owner.id = l.owner_id
left join next_action na on na.subject_type='lead' and na.subject_id=l.id and na.status='open'
left join v_last_touch lt on lt.subject_type='lead' and lt.subject_id=l.id;
grant select on v_export_leads to carr_reader;

create or replace view v_export_clients as
select c.roster_ref              as "Client ID",
       p.name                    as "Name",
       org.name                  as "Practice / Entity",
       coalesce(c.owner_label, owner.display_name) as "Owner",
       cs.label                  as "Status",
       coalesce(c.vertical,'') || coalesce(' / ' || c.subtype,'') as "Specialty / Type",
       coalesce(p.city,'') || coalesce(', ' || p.state,'')        as "Market / Location",
       (select string_agg(distinct d.deal_type, ', ') from deal d where d.client_id=c.id) as "Deal Type",
       c.acquisition_source      as "Referral Source",
       c.contact_label           as "Contact",
       p.phone                   as "Phone",
       p.email                   as "Email",
       (select string_agg(rs.source_system || ':' || rs.external_key, '; ')
          from record_source rs where rs.entity_type='client' and rs.entity_id=c.id
           and rs.source_system='dup_candidate')     as "Possible Duplicate Of",
       c.notes_path              as "Detail File"
from client c
join party p on p.id = c.party_id
join client_status cs on cs.slug = c.status
left join party org on org.id = p.org_id
left join actor owner on owner.id = c.owner_id;

drop view v_export_vendors;
create view v_export_vendors as
select v.vendor_ref              as "ID",
       p.name                    as "Name",
       org.name                  as "Company",
       v.category                as "Category",
       array_to_string(v.verticals, ', ') as "Vertical",
       p.title                   as "Title",
       coalesce(v.owner_label, owner.display_name) as "Owner",
       vs.label                  as "Stage",
       lt.last_touch             as "Last Touch",
       na.description            as "Next Step",
       case when v.referral_active then 'Yes' when not v.referral_active then 'No' end
                                 as "Referral-active?",
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
       case when v.enrich then 'Yes' when not v.enrich then 'No' end as "Enrich?",
       v.out_of_market           as "_out_of_market"
from vendor v
join party p on p.id = v.party_id
left join vendor_stage vs on vs.slug = v.stage
left join party org on org.id = p.org_id
left join actor owner on owner.id = v.owner_id
left join next_action na on na.subject_type='vendor' and na.subject_id=v.id and na.status='open'
left join v_last_touch lt on lt.subject_type='vendor' and lt.subject_id=v.id;
grant select on v_export_vendors to carr_reader;

create or replace view v_export_deals as
select d.id, d.name, d.salesforce_id, d.deal_type, ph.label as phase, d.segment,
       d.outcome, d.closed_on, d.notes_path,
       c.roster_ref as client_ref, pc.name as client_name,
       initcap(lead_actor.slug) as owner,
       d.sf_commission_placeholder as "PLACEHOLDER_sf_commission_never_sum",
       d.sf_close_date_placeholder as "PLACEHOLDER_sf_close_date_never_forecast",
       d.source_row
from deal d
join client c on c.id = d.client_id
join party pc on pc.id = c.party_id
join deal_phase ph on ph.slug = d.phase
left join deal_participant dp on dp.deal_id=d.id and dp.role='lead' and dp.to_at is null
left join actor lead_actor on lead_actor.id = dp.actor_id;
