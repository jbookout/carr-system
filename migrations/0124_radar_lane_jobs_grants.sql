-- 0124_radar_lane_jobs_grants.sql
-- THE RADAR LANE'S POOL MAPPING HAS NEVER RUN UNATTENDED, and the nightly chain
-- has been saying so in a line nobody read as a failure.
--
-- WHAT THE CHAIN PRINTS, twice a night, once per lane:
--
--   [map-radar-lane SKIP] upstream: pool mapping failed (InsufficientPrivilege:
--   permission denied for view v_export_leads) — the lane file itself was still
--   written normally. Investigate and re-run by hand: ...
--
-- It is a SKIP, not a FAIL, so the chain's exit code never carried it and no
-- incident was ever opened for it. But the lane file being written is the
-- cheap half of that step. The pool mapping — scoring the lane's rows against
-- every lead and client we already carry, suppressing the ones we know, and
-- filing the rest as candidates — is the half that produces work, and it has
-- been failing on its first query every single night.
--
-- THE CAUSE IS THE SAME ONE 0105 DOCUMENTED, on a different table. carr_jobs is
-- the unattended nightly role (0021). pipelines/map_radar_lanes.py's map_lane()
-- connects as carr_jobs (CARR_DB_JOBS_URL — ORDER 19a names it), then calls
-- import_candidate_pool.py's load_known(), whose FIRST statement is a select
-- from v_export_leads. No v_export_* view has ever been granted to carr_jobs:
-- 0009/0010/0011 each granted the view to carr_reader as they redefined it, and
-- carr_reader is not the role the job runs as.
--
-- Probed against PRODUCTION before writing a line of this file, because 0105's
-- own header is the lesson — "a rehearsal that holds different privileges than
-- production cannot test the privileges". carr_jobs held exactly one of the
-- eight privileges the mapping needs (select on ingest_inbox), so granting only
-- the view the error names would have moved the failure to the next statement
-- rather than fixed the step. Column-level grants were checked separately,
-- since has_table_privilege cannot see them: actor.id and actor.slug are
-- already column-granted to carr_jobs by 0021, so the actor lookup in the same
-- code path needs nothing here and gets nothing here.
--
-- WHAT THIS DELIBERATELY WITHHOLDS. No DELETE, on either table. A nightly job
-- may observe candidates, add them, and update the ones it already filed; it
-- may never remove one. A candidate that stops appearing in a lane file is a
-- lane that changed, not a candidate that never existed, and the suppression
-- history is the record of what we already decided about a person. Same posture
-- 0121 took with carr_exporter and ops.service, asserted below the same way:
-- a grant file that only checks what it added would pass just as happily if it
-- had granted everything.

begin;

-- The comparison set the suppressor reads: every lead and every client we
-- already carry, in the export views' own shape (import_candidate_pool.py's
-- load_known reads exactly these two, in this order).
grant select on v_export_leads, v_export_clients to carr_jobs;

-- The pool itself. select for the "have I already filed this source_key"
-- check, insert for new candidates, update for re-scoring one already filed.
grant select, insert, update on candidate_pool to carr_jobs;

-- The inbox. The lane files rows and stamps their status, which is where the
-- next failure would have landed.
--
-- SELECT IS RE-GRANTED HERE EVEN THOUGH PRODUCTION ALREADY HOLDS IT FROM 0021,
-- and the first draft of this file did not, which is how the reason was found:
-- the staging rehearsal failed on `0124 FAILED: the radar lane still cannot
-- reach ingest_inbox.SELECT`. Production and staging do not hold the same role
-- state — staging's roles were created after 0021 ran there — so a file that
-- ASSERTS eight privileges while GRANTING seven passes or fails on which
-- database it meets. A grant is idempotent; a dependency on another
-- environment's history is not. Every privilege this file asserts, it grants.
grant select, insert, update on ingest_inbox to carr_jobs;

-- Same reasoning for the system-actor lookup the pool import runs before it
-- writes. 0021 granted these exact four columns to carr_jobs for the cadence
-- engine; this re-states that grant column-for-column rather than widening it
-- to the table, so an environment where 0021's grant did not land still runs
-- the mapping, and one where it did is unchanged.
grant select (id, slug, kind, display_name) on actor to carr_jobs;

-- No sequence grants: both tables key on uuid with a gen_random_uuid() default
-- (checked in db/schema.sql), so nothing here needs the sequence dance 0121
-- had to do for ops.service.

-- ── proof, because a grant nobody has watched take effect is a hope ──────────
-- The catalog is authoritative about what a grant SAYS. 0117 documents why this
-- cannot be proven by `set role` on this Postgres — SET ROLE needs membership
-- WITH SET, and granting that to make a test pass would widen the role graph
-- for the benefit of an assertion. So this asserts against the catalog, names
-- every privilege the code path needs rather than only the ones it just added,
-- and asserts the two withheld DELETEs.
do $$
declare missing text;
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_jobs') then
    raise notice 'carr_jobs absent — grant proof skipped in this environment';
    return;
  end if;

  select string_agg(t.obj || '.' || t.priv, ', ')
    into missing
    from (values ('v_export_leads',   'SELECT'),
                 ('v_export_clients', 'SELECT'),
                 ('candidate_pool',   'SELECT'),
                 ('candidate_pool',   'INSERT'),
                 ('candidate_pool',   'UPDATE'),
                 ('ingest_inbox',     'SELECT'),
                 ('ingest_inbox',     'INSERT'),
                 ('ingest_inbox',     'UPDATE')) as t(obj, priv)
   where not has_table_privilege('carr_jobs', t.obj, t.priv);
  if missing is not null then
    raise exception '0124 FAILED: the radar lane still cannot reach %', missing;
  end if;

  -- The actor lookup in the same code path. Column-scoped, so has_table_
  -- privilege cannot see it and would report a table-level false either way.
  if not has_column_privilege('carr_jobs', 'actor', 'id', 'SELECT')
     or not has_column_privilege('carr_jobs', 'actor', 'slug', 'SELECT') then
    raise exception '0124 FAILED: carr_jobs cannot read actor.id/actor.slug — '
                    'the pool import resolves the system actor before it writes';
  end if;
  -- And the column grant must stay a COLUMN grant. Widening it to the table
  -- would hand an unattended job every column of the actor table, including
  -- whatever a later migration adds to it.
  if has_table_privilege('carr_jobs', 'actor', 'SELECT') then
    raise exception 'carr_jobs must NOT hold table-level SELECT on actor — '
                    '0021 scoped this read to four columns on purpose, and a '
                    'table-level grant silently absorbs every column added later';
  end if;

  if has_table_privilege('carr_jobs', 'candidate_pool', 'DELETE') then
    raise exception 'carr_jobs must NOT hold DELETE on candidate_pool — an '
                    'unattended job may add and re-score candidates and may '
                    'never remove one; the suppression history is the record of '
                    'what we already decided about a person';
  end if;
  if has_table_privilege('carr_jobs', 'ingest_inbox', 'DELETE') then
    raise exception 'carr_jobs must NOT hold DELETE on ingest_inbox — an intake '
                    'row is triaged, never erased';
  end if;

  raise notice '0124: carr_jobs can now read both export views and read/insert/update '
               'candidate_pool and ingest_inbox — the eight privileges the radar lane''s '
               'pool mapping needs — and holds DELETE on neither table';
end $$;

commit;
