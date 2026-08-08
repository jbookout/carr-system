-- 0079_review_clock_backfill.sql — start the review clock on imported
-- sections. The health row "never-reviewed" reported 81 policy-bearing
-- sections with no review_after (the empty-signal guard doing its job:
-- staleness machinery was inert for everything the importer landed, because
-- the importer never set the clock). Backfill from each document's policy,
-- anchored at the section's import time — an imported section's content is
-- as old as its source file's last edit, but updated_at (the import moment)
-- is the honest, available anchor and errs toward EARLIER review.
-- pipelines/doctrine_import.py sets the clock at landing from this migration
-- forward (same commit); this row covers what already landed.

begin;

update doctrine_section s
   set review_after = s.updated_at + (p.max_age_days || ' days')::interval
  from doctrine_document d
  join doctrine_review_policy p on p.id = d.review_policy_id
 where d.id = s.document_id
   and s.review_after is null
   and p.max_age_days is not null;

do $$
declare n int;
begin
  select count(*) into n
    from doctrine_section s
    join doctrine_document d on d.id = s.document_id
    join doctrine_review_policy p on p.id = d.review_policy_id
   where s.review_after is null and p.max_age_days is not null;
  if n > 0 then
    raise exception 'review-clock backfill left % sections unclocked', n;
  end if;
end $$;

commit;
