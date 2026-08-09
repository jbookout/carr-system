-- 0082_timeline_event_summary.sql — let an event row carry a HUMAN summary onto the
-- timeline, so a decision attached to a record reads as what was decided.
--
-- Loop #278. Joe, 2026-08-09: "build it".
--
-- THE DEFECT THIS UNBLOCKS, MEASURED BEFORE A LINE WAS WRITTEN. 363 decision events
-- exist in production and 363 of them carry a freshly minted random subject_id, so
-- not one is reachable from the deal, client, lead or vendor it was about:
--
--   select count(*) filter (where exists (select 1 from deal  d where d.id = e.subject_id)),
--          count(*) filter (where exists (select 1 from party p where p.id = e.subject_id))
--     from event e where e.subject_type = 'decision';   -- 0, 0 out of 363
--
-- log-decision now takes an `about` ref and mirrors a second event onto the record it
-- governs, which is what puts the ruling in catch-me-up. But the substrate catch-me-up
-- reads, v_subject_timeline, exposes only `coalesce(e.field, e.verb)` as the summary
-- and never touches new_value — so the mirrored row would land on the timeline reading
-- "log-decision" with the title nowhere in sight.
--
-- WHY THE SUMMARY GOES IN new_value AND NOT IN `field`. `field` means "which column
-- changed" and every writer in tools.js treats it as a short slug: 'status',
-- 'client_id', 'document', 'measurement', 'outcome_verdict', a comma list of changed
-- keys. Stuffing a sentence of prose into it to win one render would corrupt the one
-- column that tells a reader what a write touched. new_value is already free-form
-- jsonb per verb, so a reserved 'summary' key costs nothing and generalises: ANY verb
-- may now hand its timeline row a human sentence.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THIS IS NOT A NO-OP, AND THE FIRST DRAFT OF THIS FILE WRONGLY CLAIMED IT WAS.
--
-- The first version asserted that no event row carries a 'summary' key, so every row
-- would fall through unchanged, and it guarded on `drifted = 0`. Rehearsal on branch
-- rehearse-0082-loop278 refuted that immediately: 768 of 2,492 event rows ALREADY
-- carry one. Every one of them is an `import` row, and the sentence is the one the
-- importer itself wrote and the view has been throwing away ever since:
--
--   before 0082:  import
--   after  0082:  imported V-BNK-001 from vendors.xlsx/Vendors
--
-- Spread across the timelines a human actually reads — 290 vendor, 207 lead, 156
-- client, 75 party, 40 deal. So 0082 does change what those rows display, and the
-- change is the recovery of information that was recorded on purpose and then
-- discarded at read time. That is worth shipping, but it is NOT invisible and this
-- header will not pretend otherwise.
--
-- WHAT THE GUARD ASSERTS INSTEAD — invariants, not a row count, because a hardcoded
-- count is a number a later edit falsifies (rule b01edd26). All three verified on the
-- rehearsal branch before this file was finalised:
--   A. No row recorded before 0082 loses its `field` signal: every row whose display
--      changes had field IS NULL, so a real "which column changed" label is never
--      overwritten by a prose summary.
--   B. No row's summary becomes null or blank.
--   C. No stored summary is blank or whitespace-only.
-- The view itself carries the matching belt: nullif(btrim(...)) means a future writer
-- storing an empty summary cannot blank a timeline row, it just falls through.
--
-- Column names, types, order and grants are untouched: `create or replace view`
-- preserves the grant to carr_reader made in 0004, and the union's column names come
-- from the activity branch, which this file does not touch.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

create or replace view v_subject_timeline as
select 'activity' as entry_kind, a.id, a.occurred_at, a.recorded_at, act.slug as actor,
       a.kind as verb, a.summary, a.detail, a.owed,
       coalesce(a.deal_id, a.client_id, a.lead_id, a.vendor_id) as subject_id,
       case when a.deal_id is not null then 'deal'
            when a.client_id is not null then 'client'
            when a.lead_id is not null then 'lead'
            else 'vendor' end as subject_type
from activity a join actor act on act.id = a.actor_id
union all
select 'event', e.id, e.occurred_at, e.recorded_at, act.slug,
       -- [0082] a verb-supplied human sentence when one exists, otherwise exactly what
       -- this view returned before 0082. nullif(btrim(...)) so a blank summary falls
       -- through instead of blanking the row.
       e.verb,
       coalesce(nullif(btrim(e.new_value->>'summary'), ''), e.field, e.verb),
       e.human_quote, null,
       e.subject_id, e.subject_type
from event e join actor act on act.id = e.actor_id;

commit;

do $$
declare clobbered int; blanked int; empty_summaries int; readable int;
begin
  -- A. a prose summary never overwrites a real field label on pre-0082 history
  select count(*) into clobbered
    from event e
   where e.field is not null
     and nullif(btrim(e.new_value->>'summary'), '') is not null
     and coalesce(nullif(btrim(e.new_value->>'summary'), ''), e.field, e.verb)
         is distinct from coalesce(e.field, e.verb);
  if clobbered <> 0 then
    raise exception '0082 would overwrite the field label on % pre-existing row(s); expected 0', clobbered;
  end if;

  -- B. nothing goes blank
  select count(*) into blanked
    from event e
   where coalesce(nullif(btrim(e.new_value->>'summary'), ''), e.field, e.verb) is null;
  if blanked <> 0 then
    raise exception '0082 blanked the summary on % row(s); expected 0', blanked;
  end if;

  -- C. no stored summary is itself blank
  select count(*) into empty_summaries
    from event e
   where e.new_value ? 'summary' and btrim(coalesce(e.new_value->>'summary', '')) = '';
  if empty_summaries <> 0 then
    raise exception '0082 found % blank stored summary/summaries; expected 0', empty_summaries;
  end if;

  select count(*) into readable
    from information_schema.role_table_grants
   where table_name = 'v_subject_timeline'
     and grantee = 'carr_reader' and privilege_type = 'SELECT';
  if readable <> 1 then
    raise exception '0082 dropped carr_reader SELECT on v_subject_timeline; found % grant(s)', readable;
  end if;
end $$;
