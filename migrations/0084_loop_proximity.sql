-- 0084_loop_proximity.sql — rank open loops by HOW CLOSE THEY ARE TO DONE,
-- which is a different question from how urgent they are, and the one the
-- backlog has never been able to answer.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY. We already tier by urgency: the bell cap (3 per domain), dated rows
-- silent until their day, decision markers surfaced by the Monday brief. Every
-- one of those answers "should this be done soon". None answers "how much is
-- left". So a loop that one sentence from Joe would close reads identically to
-- one that needs a migration and a deploy, the cheap closes never float, and a
-- ~150-row backlog only grows. Source: the loop-vs-graph study 2026-08-09
-- (capture d79ff9ae), where the author names this as his own unbuilt gap —
-- "the escalation path dumps the task on me with no triage, it should rank
-- escalations by how close they got." Loop #297.
--
-- WHAT THE SIGNAL IS, AND WHY IT IS THIS ONE. blocker_class already states, in
-- the loop's own words, what stands between it and done. 0081 added it and
-- 0083 made the gate real, so every loop opened from 2026-08-09 22:20:28Z
-- forward carries one and cannot be filed without one. No new field is needed
-- and nothing has to be guessed: the ordering below is just that existing
-- field read as effort-to-close rather than as a category.
--
--   1 ruling          one sentence from Joe and it is done
--   2 human_only      one sitting of a partner's attention
--   3 other_lane      blocked on our own work, so we control the unblock
--   4 counterparty    waiting on someone outside; we can nudge, not close
--   5 external_event  waiting on a date or an event; nothing to do but wait
--   6 capability      needs something built before it can even start
--
-- ruling sits above human_only deliberately. A ruling costs Joe a sentence; a
-- human_only item costs him a sitting. Both are "waiting on a human", and
-- collapsing them would hide the cheapest closes in the system behind the
-- most expensive ones.
--
-- THE HONEST PART, AND THE REASON THIS VIEW REPORTS ITS OWN COVERAGE. At the
-- moment of writing, 148 of 154 open loops carry NO blocker_class, because the
-- gate is one day old and everything older predates it. A ranking computed
-- over a field that is 96% empty would look authoritative and be noise, which
-- is the specific failure loop #297 was filed warning about. So unclassified
-- rows are NOT scored, NOT interleaved, and NOT quietly dropped: they get
-- rank 9 and the label 'unclassified', and v_loop_proximity_coverage reports
-- the share out loud so nobody reads the ranked head as if it were the whole
-- backlog. Coverage rises on its own as old loops are touched; no backfill
-- sweep is scheduled, because classifying another session's loop from its text
-- would be guessing at what someone else meant.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

create or replace view v_loop_proximity as
select li.id,
       li.number,
       li.kind,
       li.domain,
       li.owner,
       li.marker,
       li.drift_critical,
       li.due_on,
       coalesce(li.blocker_class, 'unclassified')  as blocker_class,
       li.blocker_detail,
       case coalesce(li.blocker_class, 'unclassified')
         when 'ruling'         then 1
         when 'human_only'     then 2
         when 'other_lane'     then 3
         when 'counterparty'   then 4
         when 'external_event' then 5
         when 'capability'     then 6
         else 9
       end                                         as proximity_rank,
       case coalesce(li.blocker_class, 'unclassified')
         when 'ruling'         then 'one sentence from Joe'
         when 'human_only'     then 'one sitting of a partner'
         when 'other_lane'     then 'our own other work'
         when 'counterparty'   then 'someone outside; nudge only'
         when 'external_event' then 'a date; nothing to do'
         when 'capability'     then 'needs a build first'
         else 'UNCLASSIFIED — predates the blocker gate'
       end                                         as proximity_label,
       (li.blocker_class is null)                  as unscored,
       (current_date - li.created_at::date)        as days_open,
       lb.block_key                                as section,
       left(coalesce(li.body, li.title, ''), 160)  as gist
  from loop_item li
  join loop_block lb on lb.id = li.block_id
 where li.kind = 'open_loop'
   and li.status = 'open'
 order by proximity_rank, li.created_at;

comment on view v_loop_proximity is
  'Open loops ordered by effort-to-close, derived from blocker_class, which is '
  'a different axis from the bell/dated/decision tiers that order by urgency. '
  'BOUND ACTION: when a partner has a spare sitting, take from the top of this '
  'list, because those are the rows a single act finishes. Rank 9 rows are '
  'UNSCORED, never "furthest away" — they predate the blocker gate and nobody '
  'ever established what they are waiting on. Read '
  'v_loop_proximity_coverage before trusting the head of this list as '
  'representative of the backlog.';

create or replace view v_loop_proximity_coverage as
select count(*)                                             as open_loops,
       count(*) filter (where not unscored)                 as scored,
       count(*) filter (where unscored)                     as unscored,
       round(100.0 * count(*) filter (where not unscored)
             / nullif(count(*), 0), 1)                      as scored_pct
  from v_loop_proximity;

comment on view v_loop_proximity_coverage is
  'What share of the open backlog v_loop_proximity can actually rank. Exists '
  'so the ranking can never be read as covering more than it does. Coverage '
  'rises as old loops are touched and given a blocker; it is expected to start '
  'near 4% the day it ships. BOUND ACTION: if scored_pct is still under 50% '
  'once the backlog has turned over, the gate is being satisfied with a '
  'throwaway class rather than a real one, and the blocker vocabulary needs '
  'review rather than the ranking.';

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare
  -- v_ prefixes are deliberate: unprefixed `scored` / `scored_pct` collide with
  -- the identically named columns of v_loop_proximity_coverage, and PL/pgSQL
  -- resolves the ambiguity by refusing the query outright. Caught on the first
  -- apply of this file, 2026-08-09.
  v_total int; v_scored int; v_pct numeric; v_head_rank int; v_bad int;
begin
  select open_loops, scored, scored_pct into v_total, v_scored, v_pct
    from v_loop_proximity_coverage;

  -- Every non-null blocker_class must map to a real rank. A value that fell
  -- through to 9 while being non-null would mean the vocabulary grew and this
  -- view silently mis-sorted the new class as unscored, which is the exact
  -- failure mode this assertion exists to catch on the next migration.
  select count(*) into v_bad
    from v_loop_proximity
   where not unscored and proximity_rank = 9;
  if v_bad <> 0 then
    raise exception '0084: % row(s) carry a blocker_class this view does not rank — extend the CASE', v_bad;
  end if;

  -- Unscored rows must sort last, never interleaved with real ranks.
  select min(proximity_rank) into v_head_rank from v_loop_proximity where unscored;
  if v_head_rank is not null and v_head_rank <> 9 then
    raise exception '0084: unscored rows are sorting at rank %, expected 9', v_head_rank;
  end if;

  raise notice '0084 ok — % open loops, % scored (%%%), unscored sort last',
    v_total, v_scored, v_pct;
end $$;
