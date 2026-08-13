-- 0109_integrity_digest_unregistered_targets.sql
-- TELL AN UNREGISTERED EXPORT TARGET FROM A BROKEN ONE (loop #140, item 1).
--
-- THE CHOICE THIS ROW ASKED SOMEONE TO MAKE. Two options sat in
-- specs/v_integrity_digest-unregistered-targets.sql since 2026-08-02: (A) this
-- drop-in view replacement, written and validated against production but never
-- applied, or (B) an export_target registry table, which the specialist recommended.
-- Checked 2026-08-13: neither shipped, and no migration anywhere mentions export_target.
--
-- RULED: OPTION A. Option B would put the list of real export targets in a TABLE that
-- has to be kept in step with exporters/targets.py's TARGETS, which is already the
-- source of truth — two homes for one fact, which the consolidation rule rejects on
-- sight. The Python list is where a target is born; a table would only ever be a copy
-- that drifts. A is also the smaller structure that works, it deletes nothing, and it
-- was already validated live.
--
-- WHAT IT FIXES. export_freshness groups export_run BY TARGET, so any key that ever
-- wrote a row stays in the digest forever. Three keys sit there that no exporter owns,
-- each reporting a permanent null that reads as "no data yet" or "something is broken".
-- It is neither: nothing exports them, on purpose. Two permanent nulls in a health
-- surface is how the surface stops being read — the same failure as a permanently red
-- check. The rows are KEPT and LABELLED rather than deleted, because deleting them
-- would destroy the only record that those targets were tried and why they failed.

begin;

create or replace view v_integrity_digest as
  select 'row_counts'::text as line,
    jsonb_build_object(
      'deals',         (select count(*) from deal),
      'clients',       (select count(*) from client),
      'leads',         (select count(*) from lead),
      'vendors',       (select count(*) from vendor),
      'activities_7d', (select count(*) from activity where recorded_at > now() - interval '7 days'),
      'events_24h',    (select count(*) from event    where recorded_at > now() - interval '24 hours')
    ) as value
union all
  select 'writes_by_dell_24h'::text,
    to_jsonb((select count(*) from event e join actor a on a.id = e.actor_id
               where a.slug = 'dell' and e.recorded_at > now() - interval '24 hours'))
union all
  -- CHANGED BRANCH. `state` is always present and never null, so no reader has to guess
  -- what a null means. `stale` stays null where staleness is not a meaningful question,
  -- and `state` now says why.
  --   fresh            last ok inside 26h
  --   stale            last ok older than 26h — the nightly chain missed it
  --   never_succeeded  rows exist, not one of them ok. Either abandoned or broken;
  --                    exporters/targets.py TARGETS is the tiebreak, and health-check.py
  --                    prints it. See OPTION B for making the view able to say so itself.
  select 'export_freshness'::text,
    coalesce((
      select jsonb_object_agg(t.target, jsonb_build_object(
               'last_ok', t.last_ok,
               'stale',   case when t.last_ok is null then null
                               else t.last_ok < now() - interval '26 hours' end,
               'state',   case when t.last_ok is null then 'never_succeeded'
                               when t.last_ok < now() - interval '26 hours' then 'stale'
                               else 'fresh' end,
               'last_attempt',        t.last_any,
               'last_attempt_status', t.last_status))
        from (select target,
                     max(ran_at) filter (where status = 'ok') as last_ok,
                     max(ran_at)                              as last_any,
                     (array_agg(status order by ran_at desc))[1] as last_status
                from export_run group by target) t), '{}'::jsonb)
union all
  select 'norm_owed_open'::text,
    to_jsonb((select count(*) from availability where norm_owed))
union all
  select 'merge_queue'::text,
    to_jsonb((select count(*) from ingest_inbox where status = 'new'));

-- ---------------------------------------------------------------------------
-- OPTION B — sketch only, DO NOT APPLY from this file. It needs a numbered migration,
-- an exporter change to write the rows, and Joe's go.
--
--   create table export_target (
--     target          text primary key,
--     registered      boolean not null default true,
--     unregistered_at timestamptz,
--     reason          text
--   );
--
-- exporters/common.run_export upserts (target, registered = true) on every run; a small
-- reconcile step marks anything absent from TARGETS as registered = false with a reason.
-- The view then left-joins it and emits state 'unregistered' for those keys, plus a
-- 'never_ran' row for registered targets export_run has never seen. Both blind spots
-- close, and the answer stops depending on a Python import.

commit;
