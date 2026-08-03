-- Spec: v_integrity_digest.export_freshness — tell an unregistered target from a broken one
--
-- Status:   PROPOSED, 2026-08-02. Not applied. This file is a handoff, not a migration.
-- Type:     view replacement (CREATE OR REPLACE VIEW). No table change, no data change.
-- Evidence: the 2026-08-02 hygiene sweep. Verified live with
--             select target, max(ran_at) filter (where status='ok') from export_run group by 1;
--
-- ---------------------------------------------------------------------------
-- PROBLEM
--
-- `export_freshness` is built by grouping export_run BY TARGET, so a key that ever
-- wrote a single row stays in the digest forever. Three keys are in it today that no
-- exporter owns:
--
--   decision-history.md   2 rows, both validation_failed, no ok run
--   loop-idea-bank.md     1 row,  validation_failed,      no ok run
--   smoke                 heartbeat rows from bin/smoke-and-record.sh, not an export
--
-- The first two were registered while the files they render are STILL HAND-MAINTAINED,
-- so every nightly run failed them ("no prior ok run"), and they were then deliberately
-- commented out of exporters.targets.TARGETS. The failed rows remain. The digest reports
--
--   "decision-history.md": {"stale": null, "last_ok": null}
--   "loop-idea-bank.md":   {"stale": null, "last_ok": null}
--
-- A null there reads as "no data yet" or "something is broken". It is neither: nothing
-- exports these, on purpose, and nothing is wrong. Two permanent nulls in a health
-- surface is how the surface stops being read — the same failure mode as a permanently
-- red check.
--
-- ---------------------------------------------------------------------------
-- WHY THE VIEW AND NOT THE ROWS
--
-- Deleting the three failed export_run rows would clear the nulls and destroy the only
-- record that those targets were once tried and why they failed. The register should
-- keep its history and LABEL it. Nothing here deletes anything.
--
-- ---------------------------------------------------------------------------
-- WHY THIS IS NOT ALREADY DONE
--
-- Registration lives in Python (exporters/targets.py TARGETS), not in the database, so
-- the view has no way to ask "is this key still a target". Two options, and the second
-- is the recommendation:
--
--   OPTION A (no schema change, applied below as the default): infer it. A key with rows
--   but NO ok run in its entire history was never a working target. Label those
--   'never_succeeded' instead of leaving stale null, and let the reader decide. Honest,
--   but it cannot distinguish "unregistered on purpose" from "registered and broken from
--   day one" — which is the exact distinction asked for.
--
--   OPTION B (recommended, needs one small table): make registration a fact in the
--   database. An `export_target` table (target text primary key, registered boolean,
--   unregistered_at, reason) written by the exporter at startup, so the view can join it
--   and say 'unregistered' with authority. This is the only version that answers the
--   question correctly, and it also fixes the reverse blind spot: a target that is
--   registered but has NEVER written a row is invisible to the digest today, because the
--   digest can only see keys export_run already knows about.
--
-- Until B exists, tools/health-check.py's "Export register" section covers the same
-- ground on the reading side: it imports the live TARGETS and prints NOT A TARGET /
-- NEVER OK / STALE / NEVER RAN per key. That check is committed and running. This file
-- is what closes the gap for anyone reading the digest directly, through the MCP
-- `integrity-digest` verb or by hand.
--
-- ---------------------------------------------------------------------------
-- OPTION A — drop-in replacement, no schema change
-- Only the export_freshness branch differs from the live definition; every other branch
-- is carried over verbatim so this can be applied as one statement.

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
