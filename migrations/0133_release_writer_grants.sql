-- 0133_release_writer_grants.sql
-- ops.release could not be written by the role that writes the ops ledger.
--
-- WHAT 0131 GOT WRONG. It granted select/insert/update on ops.release to
-- carr_writer and select to carr_jobs, following the shape of the business
-- tables. But the operational ledger's writer is carr_jobs: 0115 grants it
-- insert on ops.deployment for exactly this reason, and tools/ops-record.py —
-- THE one writer for this schema — resolves its write connection from
-- CARR_DB_JOBS_URL. So every path that would ever record a release hit
-- "permission denied for table release".
--
-- FOUND THE ONLY WAY THIS KIND OF DEFECT IS FOUND: by trying to use it. The
-- table shipped on 2026-08-15, its constraints were proven against a live
-- database, its acceptance gate passed 15 of 15, and it still could not be
-- written by the process that exists to write it. Constraints were tested;
-- the grant was not.
--
-- Rule 5409731b already says this in general terms — a new table changes the
-- permission surface, so grant-check every table it touches — and this is that
-- rule being paid for rather than recited.
--
-- WHY UPDATE AND NOT JUST INSERT, unlike ops.deployment. A deployment marker is
-- append-only: a run happened and the row states what happened. A release has a
-- lifecycle — candidate, approved, deploying, complete — and the approval and
-- the read-back land after the row exists. The triggers from 0131 still police
-- every one of those transitions, so update here widens who may move a release
-- through its states, never what states are legal.
--
-- DELETE IS DELIBERATELY NOT GRANTED. A release row is the evidence that a
-- deploy was authorised; a writer that can erase it can erase the audit trail.

begin;

grant insert, update on ops.release to carr_jobs;

-- AND THE READ ITS OWN TRIGGER NEEDS, in this same migration. Granting the write
-- above is only half a permission: release_completion_requires_a_read_back fires
-- on ops.release as the writing role and reads ops.deployment to check that a
-- deployment recorded a read-back. 0115 gave carr_jobs INSERT on ops.deployment
-- and no SELECT, so the first attempt to complete a release as carr_jobs would
-- have died on a table it is allowed to write and not to read.
--
-- Caught by ops/trigger-grant-check.py in CI, which exists for exactly this and
-- states the rule in its own output: grant the read to the writing role in the
-- SAME migration that grants the write. An invoker-rights trigger runs as the
-- caller, and no rehearsal performed as the owner can ever see this.
grant select on ops.deployment to carr_jobs;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
begin
  if not has_table_privilege('carr_jobs', 'ops.release', 'insert') then
    raise exception '0133 FAILED: carr_jobs still cannot insert into ops.release';
  end if;
  if not has_table_privilege('carr_jobs', 'ops.release', 'update') then
    raise exception '0133 FAILED: carr_jobs still cannot update ops.release';
  end if;
  if has_table_privilege('carr_jobs', 'ops.release', 'delete') then
    raise exception '0133 FAILED: carr_jobs can DELETE a release — the audit trail is erasable';
  end if;
  if not has_table_privilege('carr_jobs', 'ops.deployment', 'select') then
    raise exception '0133 FAILED: carr_jobs cannot SELECT ops.deployment, which its own '
                    'completion trigger reads';
  end if;
  raise notice '0133: carr_jobs may record and advance a release, read the deployments its '
               'trigger checks, and erase nothing';
end $$;
