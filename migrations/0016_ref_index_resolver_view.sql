-- 0016: v_ref_index — the resolver surface for the read verbs (ORDER 7, amendment 11).
--
-- The bug: `find` and `resolveSubject` query base tables, but carr_reader is
-- views-only BY DESIGN, so both verbs have returned permission-denied in
-- production since build day. Fable's ruling: the security model wins and the
-- verbs adapt. Widening reader grants is rejected — views-only is what makes the
-- leak guard structural rather than a matter of remembering.
--
-- SAFE COLUMNS ONLY. No phone, no email, no notes, no notes_path. This view is
-- what a reader-scoped session can see of every subject in the system, so the
-- column list is a security boundary, not a convenience.
create view v_ref_index as
select 'lead'::text          as subject_type,
       l.id                  as subject_id,
       l.registry_ref        as ref,
       p.name                as display_name,
       org.name              as org_name,
       p.city                as city,
       p.specialty           as specialty,
       l.stage               as status,
       (p.merged_into is not null) as merged,
       null::text            as client_ref
  from lead l
  join party p on p.id = l.party_id
  left join party org on org.id = p.org_id
union all
select 'client', c.id, c.roster_ref, p.name, org.name, p.city, p.specialty, c.status,
       -- NOTE, deviation from the letter of the order and flagged in the commit:
       -- the order specifies party.merged_into. For CLIENTS that is incomplete —
       -- the nine legacy "Merged into C-###" tombstones carry client.merged_into
       -- only (the freeze's merge-pointer pass set that side), so keying on the
       -- party pointer alone would report nine tombstones as live records and let
       -- resolveSubject resolve to one. Same concept, same column, correct for the
       -- type that has its own pointer. No column added.
       (coalesce(c.merged_into, p.merged_into) is not null),
       null::text
  from client c
  join party p on p.id = c.party_id
  left join party org on org.id = p.org_id
union all
select 'vendor', v.id, v.vendor_ref, p.name, org.name, p.city, p.specialty, v.stage,
       (p.merged_into is not null), null::text
  from vendor v
  join party p on p.id = v.party_id
  left join party org on org.id = p.org_id
union all
-- Deals have no party, so the party-shaped columns are null by construction and
-- `merged` is false: a deal is never a merge tombstone.
select 'deal', d.id, null::text, d.name, null::text, null::text, null::text, d.phase,
       false, c.roster_ref
  from deal d
  left join client c on c.id = d.client_id;

grant select on v_ref_index to carr_reader;

comment on view v_ref_index is
  'Resolver surface for find/resolveSubject under the views-only reader role '
  '(amendment 11). SAFE COLUMNS ONLY — never add phone, email, notes, or any '
  'contact detail here; a reader-scoped session sees everything in this view.';
