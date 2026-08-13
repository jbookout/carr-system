-- 0100_doctrine_review_clock_batch_door.sql
--
-- The `never-reviewed` health row came back reading 3 on 2026-08-13, two days
-- after migration 0097 cleared it to 0. Read 0097 first: it diagnosed sections
-- written while their document had no review policy attached, and it explicitly
-- recorded that it was NOT fixing any write door because the null-preserving
-- else-branch is correct for that case.
--
-- THIS IS A DIFFERENT CAUSE, established before writing this rather than
-- assumed. The three sections are:
--
--   carr-mature-software-end-state-bduf  s42-council-rulings-2026-08-12
--   carr-control-room-bduf               s39-council-rulings-2026-08-12
--   carr-workspace-bduf                  s34-council-rulings-2026-08-12
--
-- All three were written 2026-08-12 through the MCP verb door, into documents
-- that ALREADY carried the standing-doctrine policy with max_age_days = 180. So
-- 0097's explanation does not cover them. The reason is that there are two write
-- doors and only one of them sets the clock:
--
--   * write-doctrine-section (doctrine.js ~line 404) reads the document's
--     max_age_days and sets review_after on every write. Always has.
--   * change-doctrine-sections, the BATCH door (~line 709), updated
--     current_revision_id, current_version, body_hash and updated_at, and never
--     touched review_after at all.
--
-- A council amendment pass goes through the batch door, so it minted three
-- permanently unwatched sections in one evening. Left alone this recurs on every
-- batch pass and the count climbs, which is precisely what the empty-signal
-- guard (rule 2b889e80) exists to expose: `stale-sections 0` would keep reading
-- healthy while the staleness machinery went inert for those rows.
--
-- THE DOOR IS FIXED IN THE SAME COMMIT AS THIS BACKFILL. doctrine.js now reads
-- the policy once per batch and applies the identical case-expression the
-- single-section door uses, preserving null when the document carries no policy.
-- Backfilling without that fix would clear the row today and refill it on the
-- next amendment pass.
--
-- WHY now() + 180 DAYS AND NOT A BACKDATED CLOCK: same reasoning as 0097. This
-- sets the value the door would have set had it been correct. Dating from the
-- original write instead would not fire anything today either (2026-08-12 + 180
-- is still months out), but it would encode a review date the policy never
-- declared.
--
-- SCOPE IS DELIBERATELY NARROW, and identical to 0097: only rows that are
-- active, whose policy names a max_age_days, and whose review_after is null. A
-- section that already has a clock is never touched, so re-running is a no-op
-- rather than a reset.
--
-- REVERSIBLE: `update doctrine_section set review_after = null where id in (...)`
-- restores the prior state exactly; no revision, snapshot or generation is
-- written.

begin;

-- Guard: refuse if the shape is not what was found. A count that has moved means
-- something else changed the table since this was read, and this migration
-- should be re-read rather than applied on faith.
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
    raise notice '0100: nothing to backfill — already applied or resolved elsewhere';
  elsif n > 200 then
    raise exception '0100: expected a bounded backfill, found % sections — re-read before applying', n;
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
