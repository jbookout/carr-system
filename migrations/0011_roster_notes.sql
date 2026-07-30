-- 0011: reconciliation round 3 finds. (a) the roster's 15th column (Notes)
-- — the truncated-header trap, caught by the diff; (b) markers living in
-- typed columns ('(enrich)' in Phone handled importer-side; '—' in Drip
-- Added) get their raw column.
alter table client add column notes text;
alter table lead   add column drip_added_raw text;

drop view v_export_clients;
create view v_export_clients as
select c.roster_ref              as "Client ID",
       p.name                    as "Name",
       org.name                  as "Practice / Entity",
       coalesce(c.owner_label, owner.display_name) as "Owner",
       cs.label                  as "Status",
       coalesce(c.vertical,'') || coalesce(' / ' || c.subtype,'') as "Specialty / Type",
       coalesce(p.city,'') || coalesce(', ' || p.state,'')        as "Market / Location",
       c.deal_type_label         as "Deal Type",
       c.acquisition_source      as "Referral Source",
       c.contact_label           as "Contact",
       p.phone                   as "Phone",
       p.email                   as "Email",
       c.possible_duplicate_label as "Possible Duplicate Of",
       c.notes_path              as "Detail File",
       c.notes                   as "Notes"
from client c
join party p on p.id = c.party_id
join client_status cs on cs.slug = c.status
left join party org on org.id = p.org_id
left join actor owner on owner.id = c.owner_id;
grant select on v_export_clients to carr_reader;

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
       coalesce(l.report_back_due::text, l.report_back_due_raw) as "Report-Back Due",
       l.drip_campaign           as "Drip Campaign",
       coalesce(l.drip_added::text, l.drip_added_raw) as "Drip Added",
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
