-- 0028: ORDER 36 (one-writer Phase B) — analysis rows + the dossier render views.
--
-- Scope is exactly what fable-orders-2026-08-01.md ORDER 36 step 3 asks for and
-- nothing else: extend the vocabulary that governs activity kind, confirm
-- v_subject_timeline, and stand up the render views the dossier exporter reads.
-- No new table. No new verb. No column invented beyond spec.
--
-- WHERE kind=analysis BELONGS (step 2's read, recorded here so the migration
-- carries its own reasoning):
--   * `event` has NO kind column at all. It is the field-level audit log
--     (verb / subject / field / old_value / new_value / cause) and has no
--     titled long-text pair. Analysis prose does not fit it.
--   * `activity` DOES: kind (vocabulary) + summary (the title) + detail (the
--     long text) + occurred_at + actor_id + the four subject FKs. That is
--     precisely one-writer-design's "kind=analysis, long text, titled".
--   Therefore analysis rows are ACTIVITY rows. Recorded as a divergence from the
--   design doc's literal phrase "stored as event rows" — the shape it describes
--   only exists on activity, and step 2 explicitly directs the schema read to
--   settle this before anything is added.

begin;

-- ─────────────────────────────────────────────────────────────────────────────
-- (a) the vocabulary
-- ─────────────────────────────────────────────────────────────────────────────
-- 0017 DROPPED activity's original CHECK and replaced it with
--   activity_kind_fkey FOREIGN KEY (kind) REFERENCES activity_kind(slug)
-- so this is the ORDER 3 ref-table pattern, verified live before writing:
--   conname=activity_kind_fkey, contype=f. There is no CHECK to extend and none
--   is added beside it.
--
-- is_contact = FALSE, deliberately. An analysis note is internal annotation, not
-- a touch — the exact ruling 0017 made for 'note' and 'task', and the defect its
-- comment describes ("the 2026-07-30 freeze shipped 13 rulings as activity rows
-- and stamped cold records warm"). Importing 20 dossiers' worth of legacy
-- analysis with is_contact=true would re-commit that defect at scale.
insert into activity_kind (slug, label, is_contact)
values ('analysis', 'Analysis', false)
on conflict (slug) do nothing;

-- ─────────────────────────────────────────────────────────────────────────────
-- (b) v_subject_timeline — CONFIRMED, NOT REBUILT
-- ─────────────────────────────────────────────────────────────────────────────
-- The view already exists (0004, "the catch-me-up substrate: one merged timeline
-- per subject") and already unions activity + event per subject. Analysis rows
-- are activity rows, so they flow into it with no change at all.
--
-- Two documented divergences from step 3's literal wording, neither an edit made
-- here, both flagged for ratification rather than improvised around:
--   1. It does NOT "union structured record fields". Adding record columns to it
--      would change catch-me-up's substrate — eight consumers deep — for a
--      render concern. Every other exporter in this repo reads the record fields
--      directly; the dossier renderer does the same, through (d) below.
--   2. It carries no ORDER BY. Views don't; ordering is the reading query's job.
--      Newest-first is supplied by recency_rank in (c).
do $$
declare n int;
begin
  select count(*) into n from pg_views
   where schemaname='public' and viewname='v_subject_timeline';
  if n <> 1 then
    raise exception 'v_subject_timeline is absent — ORDER 36 step 3 cannot confirm it';
  end if;
  raise notice 'v_subject_timeline confirmed present (0004), unchanged by 0028';
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- (c) notes_path normalisation — the render targets have to resolve
-- ─────────────────────────────────────────────────────────────────────────────
-- DEVIATION, FLAGGED (see the execution log). Not a step in ORDER 36. It is
-- required for step 5's "preserve the existing 20 dossier filenames/paths":
-- client.notes_path holds THREE spellings of one folder —
--   DNA/Clients/prospects/…   5 rows
--   Prospects/…               7 rows   (capital P: the retired Joe-personal
--                                       folder; these files are NOT there)
--   prospects/…               8 rows   (bare relative)
-- All 20 basenames resolve to real files in DNA/Clients/prospects/, verified by
-- directory listing on 2026-08-01. lead.notes_path's 15 rows are already
-- correct and are left untouched. Without this the exporter would write to two
-- non-existent directories and split one dossier's rows across two partitions.
update client
   set notes_path = 'DNA/Clients/prospects/' || regexp_replace(notes_path, '^.*/', '')
 where notes_path is not null
   and notes_path <> 'DNA/Clients/prospects/' || regexp_replace(notes_path, '^.*/', '');

do $$
declare total int; distinct_paths int; bad int;
begin
  select count(*) into total from client where notes_path is not null;
  select count(distinct notes_path) into distinct_paths from client where notes_path is not null;
  select count(*) into bad from client
   where notes_path is not null and notes_path not like 'DNA/Clients/prospects/%';
  if total <> 20 then
    raise exception 'expected 20 clients carrying notes_path, found % — the dossier set moved; stop and report', total;
  end if;
  if distinct_paths <> 20 then
    raise exception 'notes_path collision after normalisation: % distinct for % rows', distinct_paths, total;
  end if;
  if bad <> 0 then raise exception '% client notes_path rows still outside DNA/Clients/prospects/', bad; end if;
  raise notice 'notes_path normalised: 20 clients, 20 distinct paths, all under DNA/Clients/prospects/';
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- (d) the dossier subject view — the structured header, from record fields
-- ─────────────────────────────────────────────────────────────────────────────
-- One row per dossier file. Client-side is the spine (all 20 carry a client);
-- the lead row, where one points at the same file, is joined on so the render
-- can carry the registry ref the dossiers already print.
create view v_export_dossier_subject as
select c.notes_path                                as rel_path,
       regexp_replace(c.notes_path, '^.*/', '')    as file_name,
       c.id                                        as client_id,
       c.roster_ref                                as client_ref,
       p.name                                      as subject_name,
       c.status                                    as client_status,
       c.vertical,
       c.subtype,
       c.specialty_type_label,
       c.client_type,
       c.deal_type_label,
       c.owner_label,
       c.contact_label,
       c.acquisition_source,
       c.acquisition_detail,
       c.etl_status,
       c.notes                                     as record_notes,
       c.updated_at                                as record_updated_at,
       l.id                                        as lead_id,
       lt.last_touch
  from client c
  join party p on p.id = c.party_id
  left join lead l
    on l.notes_path is not null
   and regexp_replace(l.notes_path, '^.*/', '') = regexp_replace(c.notes_path, '^.*/', '')
  left join v_last_touch lt
    on lt.subject_type = 'client' and lt.subject_id = c.id
 where c.notes_path is not null;

comment on view v_export_dossier_subject is
  '0028 (ORDER 36 Phase B): one row per hand-maintained dossier file — the 20 '
  'clients carrying notes_path. Supplies the render''s structured header from '
  'record fields. Read-only render surface; NOT a Joe-browse surface (ruling 6).';

grant select on v_export_dossier_subject to carr_exporter;

-- ─────────────────────────────────────────────────────────────────────────────
-- (e) the analysis stream, newest-first
-- ─────────────────────────────────────────────────────────────────────────────
-- recency_rank = 1 is the newest analysis on that dossier: the render prints it
-- in full and collapses every rank > 1 to title + date + author. Both the
-- imported legacy rows and anything written live land in the same partition, so
-- one live write demotes the previous newest with no exporter change — which is
-- exactly what ORDER 36's second done-test exercises.
create view v_export_dossier_analysis as
with dossier as (
  select c.id as subject_id, 'client'::text as subject_type, c.notes_path as rel_path
    from client c where c.notes_path is not null
  union all
  select l.id, 'lead', 'DNA/Clients/prospects/' || regexp_replace(l.notes_path, '^.*/', '')
    from lead l where l.notes_path is not null
)
select d.rel_path,
       d.subject_type,
       d.subject_id,
       a.id                as analysis_id,
       a.occurred_at,
       a.recorded_at,
       a.summary           as title,
       a.detail            as body,
       a.owed,
       act.slug            as author,
       a.source,
       row_number() over (partition by d.rel_path
                          order by a.occurred_at desc, a.recorded_at desc, a.id) as recency_rank
  from dossier d
  join activity a
    on (d.subject_type = 'client' and a.client_id = d.subject_id)
    or (d.subject_type = 'lead'   and a.lead_id   = d.subject_id)
  join actor act on act.id = a.actor_id
 where a.kind = 'analysis';

comment on view v_export_dossier_analysis is
  '0028 (ORDER 36 Phase B): kind=analysis activity rows per dossier file, '
  'newest-first via recency_rank. rank 1 renders in full, ranks > 1 collapse to '
  'title + date + author. Legacy imported rows and live verb writes share one '
  'partition by design.';

grant select on v_export_dossier_analysis to carr_exporter;

-- ─────────────────────────────────────────────────────────────────────────────
-- (f) guards — prove the claims inside the migration, per house pattern
-- ─────────────────────────────────────────────────────────────────────────────
do $$
declare n int;
begin
  select count(*) into n from activity_kind where slug='analysis' and is_contact = false;
  if n <> 1 then raise exception 'activity_kind analysis row missing or is_contact=true'; end if;

  -- The amendment-10 guard, restated: analysis must never move Last Touch.
  -- v_last_touch counts is_contact kinds plus the legacy import exception; an
  -- is_contact=false slug is structurally excluded. Assert it rather than assume.
  select count(*) into n
    from activity a join activity_kind k on k.slug=a.kind
   where a.kind='analysis' and k.is_contact;
  if n <> 0 then raise exception 'analysis rows are counting as contact touches'; end if;

  select count(*) into n from pg_views where schemaname='public'
   and viewname in ('v_export_dossier_subject','v_export_dossier_analysis');
  if n <> 2 then raise exception 'dossier render views not created (% of 2)', n; end if;

  select count(*) into n from information_schema.role_table_grants
   where grantee='carr_exporter' and privilege_type='SELECT'
     and table_name in ('v_export_dossier_subject','v_export_dossier_analysis');
  if n <> 2 then raise exception 'carr_exporter SELECT grants incomplete (% of 2)', n; end if;

  -- Views-only leak guard, same as 0024: the render role never gets the base.
  select count(*) into n from information_schema.role_table_grants
   where grantee='carr_exporter' and table_name='activity';
  if n <> 0 then raise exception 'carr_exporter holds a BASE TABLE grant on activity'; end if;

  raise notice '0028 guards passed';
end $$;

commit;
