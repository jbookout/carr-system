-- 0034_coverage_and_denominators.sql — make "nothing to report" distinguishable from
-- "this detector cannot see."
--
-- Joe, 2026-08-02: "how does the system prevent this from happening again"
--
-- THE DEFECT CLASS, stated once: every failure found tonight was an EMPTY SIGNAL READ AS
-- A HEALTHY SIGNAL.
--   stale-records returned []          -> read as "nothing is stale" (it was blind)
--   smoke went silent since 7/30       -> read as "canary fine"      (nothing wrote it)
--   exports reported one FAIL          -> was five files stale       (all() short-circuit)
--   the graph rendered 134 edges       -> read as "a graph"          (0.18 edges/node)
--   3% of subjects had any last_touch  -> nobody ever counted it
--
-- A detector that says nothing must PROVE IT COULD HAVE SAID SOMETHING. That means every
-- detector publishes its DENOMINATOR alongside its result, and coverage is a first-class
-- number on the daily heartbeat where a human sees it — not a thing discovered in an audit.
--
-- This migration adds no table and no column. Two views, both read-only.

begin;

-- ── v_capture_coverage: the number whose absence hid the capture gap for weeks ──
-- Per subject type: how many records exist, how many have EVER been contacted, and how
-- many in the last 90 days. If "ever" is near zero, the pipeline is not quiet — it is
-- unrecorded, and every downstream detector is guessing.
create or replace view v_capture_coverage as
with subj as (
  select 'deal'::text   as subject_type, d.id from deal d
   where d.outcome is null and d.phase <> 'closed'
  union all select 'client', c.id from client c where c.merged_into is null
  union all select 'lead',   l.id from lead   l
  union all select 'vendor', v.id from vendor v
)
select s.subject_type,
       count(*)                                                          as records,
       count(lt.last_touch)                                              as with_any_touch,
       round(100.0 * count(lt.last_touch) / nullif(count(*),0), 1)       as pct_with_touch,
       count(*) filter (where lt.last_touch >= current_date - 90)        as touched_90d,
       round(100.0 * count(*) filter (where lt.last_touch >= current_date - 90)
             / nullif(count(*),0), 1)                                    as pct_touched_90d,
       max(lt.last_touch)                                                as most_recent
  from subj s
  left join v_last_touch lt
         on lt.subject_type = s.subject_type and lt.subject_id = s.id
 group by s.subject_type;

comment on view v_capture_coverage is
  'Capture coverage per subject type. Added 0034 after a night in which every broken '
  'detector reported all-clear. pct_with_touch is the honest health of the input layer: '
  'when it is low, staleness / reciprocity / delivery scoring / the graph are all guessing, '
  'and their silence means nothing.';

-- ── v_detector_health: each detector alongside the denominator it searched ──
-- A detector returning 0 hits out of a 0-row searchable population is BLIND, not clear.
-- That distinction is the entire lesson of 2026-08-02 and it is now computed, not recalled.
create or replace view v_detector_health as
select 'stale-records'::text as detector,
       (select count(*) from v_stale_records)                                as hits,
       (select count(*) from deal where outcome is null and phase <> 'closed') as searchable,
       (select count(*) from v_last_touch where subject_type='deal')         as with_input,
       case when (select count(*) from v_last_touch where subject_type='deal') = 0
            then 'BLIND — no deal carries a last_touch, so silence proves nothing'
            when (select count(*) from v_stale_records) = 0
            then 'CLEAR — evaluated against real input'
            else 'REPORTING' end                                            as verdict
union all
select 'today-triage',
       (select count(*) from v_today_triage),
       (select count(*) from next_action where status='open'),
       (select count(*) from next_action where status='open' and due_on is not null),
       case when (select count(*) from next_action where status='open' and due_on is not null) = 0
            then 'BLIND — no open action carries a due date'
            when (select count(*) from v_today_triage) = 0 then 'CLEAR — evaluated against real input'
            else 'REPORTING' end
union all
select 'capture-coverage',
       (select count(*) from v_capture_coverage where pct_with_touch < 25),
       (select count(*) from v_capture_coverage),
       (select coalesce(sum(with_any_touch),0) from v_capture_coverage),
       case when (select count(*) from v_capture_coverage where pct_with_touch < 25) > 0
            then 'DEGRADED — a subject type is under 25% touch coverage; detectors downstream are guessing'
            else 'CLEAR — evaluated against real input' end;

comment on view v_detector_health is
  'Every detector with the DENOMINATOR it searched. A zero result against a zero-input '
  'population is BLIND, never CLEAR. Added 0034 because stale-records reported all-clear '
  'for two days while every deal it could have flagged had a falsified last_touch.';

commit;

do $$
declare blind int;
begin
  select count(*) into blind from v_detector_health where verdict like 'BLIND%' or verdict like 'DEGRADED%';
  raise notice '0034 live. detectors currently BLIND or DEGRADED: %', blind;
end $$;
