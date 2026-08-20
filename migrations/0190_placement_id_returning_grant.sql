-- 0190_placement_id_returning_grant.sql
-- Completes 0189. That migration moved the metrics pull's READS onto collector
-- views and granted select on content_piece(id) for the status catch-up UPDATE.
-- It missed a second place Postgres requires a column read: `insert into
-- placement (...) returning id`. RETURNING projects a column, so it needs select
-- on that column even though the statement is an INSERT. Applied to production
-- 2026-08-19, the pull cleared its reads and then died one statement later on
--   psycopg.errors.InsufficientPrivilege: permission denied for table placement
-- at pull_placement_metrics.py:322.
--
-- content_piece has the same shape at line 316 and already works, because 0189
-- happened to grant its id column for the UPDATE. placement never got one.
--
-- Same posture as 0189 and for the same reason: a COLUMN grant, not a table
-- grant. ops/control-plane-db-gate.py fails the build if carr_jobs holds
-- table-wide select on placement, and a column-level grant does not set
-- has_table_privilege, so the boundary is untouched. Only the surrogate key is
-- exposed; every other column on placement stays unreadable.

begin;

grant select (id) on public.placement to carr_jobs;

commit;

do $$
begin
  if not has_column_privilege('carr_jobs','public.placement','id','select') then
    raise exception '0190 FAILED: carr_jobs cannot read placement.id, so INSERT ... RETURNING id still fails';
  end if;

  -- the boundary control-plane-db-gate.py enforces must be untouched
  if has_table_privilege('carr_jobs','public.placement','select') then
    raise exception '0190 FAILED: carr_jobs gained table-wide select on placement';
  end if;
  if has_table_privilege('carr_jobs','public.content_piece','select') then
    raise exception '0190 FAILED: carr_jobs gained table-wide select on content_piece';
  end if;

  -- and nothing beyond the surrogate key may have come with it
  if has_column_privilege('carr_jobs','public.placement','url','select')
     or has_column_privilege('carr_jobs','public.placement','external_id','select') then
    raise exception '0190 FAILED: placement column grant is wider than the id column';
  end if;
end $$;
