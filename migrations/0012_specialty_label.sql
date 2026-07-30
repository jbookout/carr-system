-- 0012: final reconciliation fix. Splitting roster "Specialty / Type" on '/'
-- corrupts prose values that contain slashes ("MS/AL/TN"). Verbatim label
-- carries fidelity; vertical/subtype stay best-effort structure.
alter table client add column specialty_type_label text;

drop view v_export_clients;
create view v_export_clients as
select c.roster_ref              as "Client ID",
       p.name                    as "Name",
       org.name                  as "Practice / Entity",
       coalesce(c.owner_label, owner.display_name) as "Owner",
       cs.label                  as "Status",
       c.specialty_type_label    as "Specialty / Type",
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
