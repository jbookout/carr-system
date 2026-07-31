-- 0013: amendment 0 (design-amendments-2026-07-30.md) — the active book becomes
-- DERIVED, and the roster export stops inventing rows.
--
-- Three changes, one migration:
--   A. v_export_clients excludes ref-less clients. The deals-JSON import created
--      12 clients with no roster_ref (no C-ID). Exporting them appended 12
--      blank-key rows to the roster — rows the reconciler could not even see.
--      A generated file must never mint records the source never had; those 12
--      stay DB-only until Joe rules on each (most look like duplicates of
--      existing C-refs: First Call DPC vs C-126, Gulf Coast vs C-112, ...).
--   B. client_status gains is_active_pipeline. Membership in the active book is
--      DERIVED, never stored -- no active_index flag on client. The only stored
--      thing is a property of the STATUS VOCABULARY: does this status, on its
--      own, mean "in the pipeline". Joe sets these; default false is the safe
--      side (a status nobody marked cannot silently inflate the book).
--   C. v_export_clients_active v2: client-shaped, one row per client.
--      v1 was `from deal d join client c` -- one row per DEAL. A client with 13
--      open deals rendered as 13 identical rows (C-131 did exactly that), and
--      clients with no deal row vanished entirely. The shape was the bug.

-- ── A. roster export: no invented rows ───────────────────────────────────────
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
left join actor owner on owner.id = c.owner_id
where c.roster_ref is not null;
grant select on v_export_clients to carr_reader;

-- ── B. the one stored bit: which statuses mean "in the pipeline" ─────────────
alter table client_status add column is_active_pipeline boolean not null default false;
comment on column client_status.is_active_pipeline is
  'Does this status alone put a client in the active book? Membership is derived '
  '(open deal OR this flag) -- never stored per-client. Joe owns these values.';

-- ── C. the active book, derived and client-shaped ───────────────────────────
drop view v_export_clients_active;
create view v_export_clients_active as
select coalesce(c.owner_label, owner.display_name)          as "Owner",
       pc.name                                              as "Name",
       c.roster_ref                                         as "C-ID",
       cs.label                                             as "Status",
       c.deal_type_label                                    as "Deal Type",
       coalesce(c.specialty_type_label, c.vertical, '')     as "Specialty",
       coalesce(pc.city,'') || coalesce(', ' || pc.state,'') as "Location",
       lt.last_touch                                        as "Last Touch",
       na.description                                       as "Next Step",
       c.notes_path                                         as "Detail"
from client c
join party pc on pc.id = c.party_id
join client_status cs on cs.slug = c.status
left join actor owner on owner.id = c.owner_id
left join v_last_touch lt on lt.subject_type = 'client' and lt.subject_id = c.id
-- LATERAL + limit 1 is load-bearing, not style: next_action allows one open ball
-- PER OWNER per subject, so a plain join re-introduces the row multiplication
-- this migration exists to kill. Prefer the client's own owner's ball; fall back
-- to any open one so an unclaimed client still shows its next step.
left join lateral (
    select n.description
    from next_action n
    where n.subject_type = 'client' and n.subject_id = c.id and n.status = 'open'
    order by (n.owner_id = c.owner_id) desc nulls last, n.due_on nulls last, n.created_at
    limit 1
) na on true
where c.merged_into is null              -- merge tombstones are not active clients
  and c.roster_ref is not null           -- same rule as the roster: no invented rows
  and (cs.is_active_pipeline
       or exists (select 1 from deal d
                   where d.client_id = c.id
                     and d.outcome is null and d.phase <> 'closed'));
grant select on v_export_clients_active to carr_reader;
