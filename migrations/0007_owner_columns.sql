-- 0007: faithful Owner columns for lead + client (import forcing function).
-- Registry/roster Owner values include strings that are not actors ("Shared",
-- "unassigned"). owner_label preserves the original string byte-faithfully;
-- owner_id links the mapped actor when one exists. Export views prefer the
-- label (fidelity), fall back to the actor slug.
alter table lead   add column owner_id uuid references actor(id);
alter table lead   add column owner_label text;
alter table client add column owner_id uuid references actor(id);
alter table client add column owner_label text;

create or replace view v_export_leads as
select l.registry_ref            as "Lead ID",
       l.created_at::date        as "Date In",
       coalesce(l.owner_label, owner.display_name) as "Owner",
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
       l.suppressed              as "_suppressed"
from lead l
join party p on p.id = l.party_id
left join party org on org.id = p.org_id
left join actor owner on owner.id = l.owner_id
left join next_action na on na.subject_type='lead' and na.subject_id=l.id and na.status='open'
left join v_last_touch lt on lt.subject_type='lead' and lt.subject_id=l.id;

create or replace view v_export_clients as
select c.roster_ref              as "Client ID",
       p.name                    as "Name",
       org.name                  as "Practice / Entity",
       coalesce(c.owner_label, owner.display_name) as "Owner",
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
left join actor owner on owner.id = c.owner_id;
