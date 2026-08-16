-- 0136_release_manifest_view_grant.sql
-- `ops-record.py release show` could not read back a release the same tool
-- had just written.
--
-- WHAT WAS MISSING. 0131 granted select on ops.v_release_manifest to
-- carr_reader and carr_writer. 0133 then established what 0131 had got wrong
-- about roles — the operational ledger's writer is carr_jobs, and
-- tools/ops-record.py resolves its connection from CARR_DB_JOBS_URL — and it
-- fixed the TABLE grants accordingly. It did not fix the VIEW. So carr_jobs
-- could insert a release, update it through its lifecycle, and never once look
-- at it: `release show` died with "permission denied for view
-- v_release_manifest".
--
-- This is 0133's own defect class recurring one object over, which is worth
-- saying plainly rather than filing quietly. 0133's header names the lesson —
-- "Constraints were tested; the grant was not" — and cites rule 5409731b, that
-- a new table changes the permission surface of every verb touching it, so
-- grant-check every table. The word doing the damage there is TABLE. A view is
-- a separate privilege object and was outside the sweep both times.
--
-- HOW IT WAS FOUND, and why the read matters more than it looks. On 2026-08-16
-- a session recorded a release candidate for the Hermes read-only door without
-- its three evidence refs, then handed the approval to Joe. His approve command
-- failed on the constraint that an approved release carries its evidence
-- (defect 61d2f0f8). The session could not have caught its own bad write
-- beforehand, because the one command that reads a release row back was
-- unusable to it. A write path with no read path means every row it produces is
-- unverifiable by the thing that wrote it, and the first reader is a human
-- hitting an error on a step reserved for him.
--
-- SELECT ONLY, and only this view. carr_jobs already holds insert and update on
-- ops.release from 0133 and deliberately holds no delete, since a release row
-- is the evidence a deploy was authorised. Nothing here widens what may be
-- written or which state transitions are legal; the 0131 triggers still police
-- those. It grants the ability to look.

begin;

grant select on ops.v_release_manifest to carr_jobs;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
begin
  if not has_table_privilege('carr_jobs', 'ops.v_release_manifest', 'select') then
    raise exception '0136 FAILED: carr_jobs still cannot select from ops.v_release_manifest';
  end if;
  -- The write grants 0133 established must survive this migration untouched.
  if not has_table_privilege('carr_jobs', 'ops.release', 'insert') then
    raise exception '0136 FAILED: carr_jobs lost insert on ops.release';
  end if;
  if not has_table_privilege('carr_jobs', 'ops.release', 'update') then
    raise exception '0136 FAILED: carr_jobs lost update on ops.release';
  end if;
  -- And the one that must never be granted stays ungranted.
  if has_table_privilege('carr_jobs', 'ops.release', 'delete') then
    raise exception '0136 FAILED: carr_jobs must never hold delete on ops.release';
  end if;
  raise notice '0136 OK: carr_jobs can read ops.v_release_manifest; write grants unchanged';
end $$;
