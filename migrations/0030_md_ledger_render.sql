-- 0030_md_ledger_render.sql — the render view behind ORDER 39's two ledger renders.
--
-- ORDER 39 step 4 asks for a render per ledger "reflecting its imported data".
-- This view is that source. It adds NO table and NO column: the ledger entries
-- are ordinary `activity` rows (source='import', author preserved) and the only
-- thing missing was a way to read them back as a ledger rather than as scattered
-- touches. record_source's external_key already encodes which ledger and which
-- entry each row came from, so the view unpacks that key and joins the subject.
--
-- WHAT THE RENDER WILL AND WILL NOT SAY, stated here because the next reader
-- will otherwise assume the render replaces the file. It does not, yet. Two of
-- the nine entries in the two source ledgers are imported; seven are parked or
-- review-listed on stop rules (unwritable `automation` author, unstamped
-- author/date, no subject ref, duplicate un-merged vendors, hand-maintained
-- counts the source file itself says to derive). A render built on this view is
-- therefore a PARTIAL view of either ledger until those seven are ruled on, and
-- the exporter prints that fact in the file header rather than letting a short
-- render read as a complete one. Neither .md file may be retired to a pointer
-- header until then — done-test 4 requires a pointer that resolves to a real and
-- CURRENT data location, and today it would not.
--
-- Idempotency and provenance live in the importer (pipelines/import_md_ledgers.py),
-- not here. This file is read-only plumbing.
--
-- NOT APPLIED ANYWHERE. Rehearsed on a disposable Neon branch only; production
-- apply is the supervisor's/Joe's tap, per the stream preamble.

begin;

-- One ref per subject. v_ref_index can carry more than one row for a subject
-- (a person who exists as both a lead and a client — Tyrer does, L-208 and
-- C-155, deliberately un-merged), and without the distinct on, a single ledger
-- entry would render twice and read as two entries. Ordering by ref keeps the
-- choice deterministic across runs rather than whatever the planner returns.
create or replace view v_md_ledger_entry as
select
    rs.external_key,
    split_part(rs.external_key, '#', 1)                          as ledger,
    split_part(split_part(rs.external_key, '#', 2), ':', 1)      as entry_id,
    a.occurred_at::date                                          as entry_date,
    act.slug                                                     as author,
    ri.ref                                                       as subject_ref,
    ri.display_name                                              as subject_name,
    ri.subject_type,
    a.summary,
    a.detail,
    a.id                                                         as activity_id
from record_source rs
join activity a   on a.id = rs.entity_id and rs.entity_type = 'activity'
join actor   act  on act.id = a.actor_id
left join lateral (
    select r.ref, r.display_name, r.subject_type
      from v_ref_index r
     where r.subject_id = coalesce(a.vendor_id, a.client_id, a.lead_id, a.deal_id)
     order by r.ref
     limit 1
) ri on true
where rs.source_system = 'md-ledger';

comment on view v_md_ledger_entry is
  'ORDER 39. Imported markdown-ledger entries (hunt-ledger, deals-reciprocity) read '
  'back as ledger rows. One row per (entry, subject) — a hunt entry touching five '
  'subjects is five rows, which is what the import wrote. PARTIAL by design until '
  'the seven parked/review-listed entries are ruled on.';

-- Reader-safe by construction: every column is either a ledger field, a ref, or a
-- display name — the same columns v_ref_index already exposes to carr_reader. No
-- actor uuids, no subject uuids beyond the activity id, no contact detail.
grant select on v_md_ledger_entry to carr_reader;
grant select on v_md_ledger_entry to carr_writer;
grant select on v_md_ledger_entry to carr_exporter;

-- ── guard block: assert the end state rather than trusting it ────────────────
do $$
declare n int;
begin
  -- the view exists and is selectable
  perform 1 from pg_views where viewname = 'v_md_ledger_entry';
  if not found then
    raise exception '0030: v_md_ledger_entry was not created';
  end if;

  -- one row per (entry, subject), never a fan-out from the ref join
  select count(*) into n from (
    select external_key, count(*) c from v_md_ledger_entry group by 1 having count(*) > 1
  ) dup;
  if n > 0 then
    raise exception '0030: % external_key(s) render more than once — the ref join fanned out', n;
  end if;

  -- every rendered row carries a preserved author and a real date
  select count(*) into n from v_md_ledger_entry
   where author is null or entry_date is null;
  if n > 0 then
    raise exception '0030: % ledger row(s) lost author or date — provenance is the point', n;
  end if;

  -- the view only ever shows imported rows, never a 'stated' touch
  select count(*) into n from v_md_ledger_entry v
    join activity a on a.id = v.activity_id where a.source <> 'import';
  if n > 0 then
    raise exception '0030: % row(s) in the ledger view are not source=import', n;
  end if;
end $$;

commit;
