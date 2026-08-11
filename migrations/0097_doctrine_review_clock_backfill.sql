-- 0097_doctrine_review_clock_backfill.sql
--
-- The `never-reviewed` health row had read 68 for days: active sections whose
-- document carries a review policy WITH a max_age_days, but whose own
-- review_after is null.  That row is the empty-signal guard for the row above
-- it — `stale-sections 0` reads healthy whether the staleness machinery is
-- working or simply inert, and for these 68 it was inert.
--
-- WHY THEY ARE NULL, established before writing this rather than assumed.
-- Both write doors already set the clock correctly:
--   * pipelines/doctrine_import.py line 137, and
--   * mcp-server/src/doctrine.js line 404 (write-doctrine-section),
-- both compute `now() + (policy_days || ' days')::interval`, and both guard it
-- with `case when policy_days is not null ... else review_after end`.  That
-- else-branch PRESERVES NULL.  So a section written while its document had no
-- review policy attached keeps a null clock forever, even after the policy is
-- attached later, unless something rewrites that section.  Sections nobody
-- edits again therefore stay permanently unwatched.
--
-- WHAT IS AFFECTED, read-only inventory taken 2026-08-11 before this was written:
--   CARR Control Room — Big Design Up Front   standing-doctrine  180d   37 sections
--   CARR Workspace   — Big Design Up Front    standing-doctrine  180d   31 sections
-- Two documents, 68 sections, one policy, one max_age_days.  Both were authored
-- through the verb door by Joe's Codex session and had their policy attached
-- after their sections were written, which is the exact shape described above.
--
-- WHY now() + 180 DAYS AND NOT A BACKDATED CLOCK.  This sets the same value the
-- two doors would have set had the policy been attached first — it does not
-- invent a review date, it applies the document's own declared policy.  Dating
-- the clock from the sections' original write instead would fire a review
-- immediately for content nobody has flagged as suspect, converting a silent
-- gap into 68 noisy rows: the same disease in the other direction.
--
-- SCOPE IS DELIBERATELY NARROW.  Only rows that are active, whose policy names
-- a max_age_days, and whose review_after is null.  A section that already has a
-- clock is never touched, so re-running this is a no-op rather than a reset —
-- it cannot silently push an existing review date into the future.
--
-- REVERSIBLE: `update doctrine_section set review_after = null where id in (...)`
-- restores the prior state exactly; nothing else about the sections changes and
-- no revision, snapshot or generation is written.
--
-- NOT FIXED HERE, deliberately: the else-branch that preserves null is correct
-- for its own case (a document with no policy should not gain a clock), so the
-- durable guard belongs where a policy is ATTACHED to a document, not in these
-- two update statements.  Recorded rather than patched blind.

begin;

-- Guard: refuse if the shape is not what the inventory found.  A count that has
-- moved means something else changed the table since this was reviewed, and
-- this migration should be re-read rather than applied on faith.
do $$
declare
  n int;
begin
  select count(*) into n
    from doctrine_section s
    join doctrine_document d on d.id = s.document_id
    join doctrine_review_policy p on p.id = d.review_policy_id
   where s.status = 'active'
     and p.max_age_days is not null
     and s.review_after is null;
  if n = 0 then
    raise notice '0097: nothing to backfill — already applied or resolved elsewhere';
  elsif n > 200 then
    raise exception '0097: expected a bounded backfill, found % sections — re-read before applying', n;
  end if;
end $$;

update doctrine_section s
   set review_after = now() + (p.max_age_days || ' days')::interval
  from doctrine_document d
  join doctrine_review_policy p on p.id = d.review_policy_id
 where s.document_id = d.id
   and s.status = 'active'
   and p.max_age_days is not null
   and s.review_after is null;

commit;
