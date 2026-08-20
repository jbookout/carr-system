-- 0189_social_pull_collector_views.sql
-- The weekly metrics pull (pipelines/pull_placement_metrics.py) still read the
-- raw source tables after the control plane moved that access behind collector
-- views. Since that boundary took effect the pull dies at its first statement
-- with `permission denied for table placement`, which means NO placement row is
-- created for anything published, which means those posts can never be measured.
--
-- This is NOT a lost grant and the fix is NOT to restore one. ops/control-plane
-- -db-gate.py fails the build if carr_jobs holds table-wide select on
-- content_piece, placement, candidate_pool, v_expired_verification or v_loops.
-- That boundary is deliberate and stays. What was missing is a projection for
-- the one lane that still needed identity data, so this adds two, in the same
-- shape as 0162's collector views: definer views, minimal columns, no PII.
--
--   v_control_plane_social_placement_identity — the dedup map the pull builds on
--   every run to decide which Blotato posts it has already recorded. Carries the
--   placement id, its piece, its external id and platform. No url, no body, no
--   timestamps beyond what identity needs.
--
--   v_control_plane_social_measured_pieces — the pieces whose placements have at
--   least one metric, with the status and source label the pull filters on. It
--   exposes the source label as a column rather than hardcoding the pipeline's
--   constant, so the view stays generic and the caller keeps deciding.
--
-- Plus ONE column-scoped grant. `update content_piece ... where id in (...)`
-- reads content_piece.id, and Postgres requires select on a column named in an
-- UPDATE's WHERE clause. A column grant is exactly what secrets-inventory.md
-- already describes for this role ("reads are column-scoped so no phone, email
-- or notes column is reachable"), and it does NOT trip the gate: the gate tests
-- has_table_privilege, which stays false for a column-level grant. The
-- verification block at the bottom asserts both halves of that, so a future
-- change that widens this to a table grant fails here rather than in CI.

begin;

create or replace view public.v_control_plane_social_placement_identity as
select p.id as placement_id,
       p.piece_id,
       p.external_id,
       p.platform
  from public.placement p
 where p.external_id is not null;

create or replace view public.v_control_plane_social_measured_pieces as
select distinct c.id as piece_id,
       c.status,
       c.features->>'source' as piece_source
  from public.content_piece c
  join public.placement p on p.piece_id = c.id
  join public.placement_metric m on m.placement_id = p.id;

grant select on public.v_control_plane_social_placement_identity,
                public.v_control_plane_social_measured_pieces
  to carr_jobs;

grant select (id) on public.content_piece to carr_jobs;

commit;

do $$
declare relation text;
begin
  -- the new projections must be readable by the pull
  foreach relation in array array[
    'public.v_control_plane_social_placement_identity',
    'public.v_control_plane_social_measured_pieces'
  ] loop
    if not has_table_privilege('carr_jobs',relation,'select') then
      raise exception '0189 FAILED: carr_jobs cannot read %',relation;
    end if;
  end loop;

  -- the column grant must land ...
  if not has_column_privilege('carr_jobs','public.content_piece','id','select') then
    raise exception '0189 FAILED: carr_jobs cannot read content_piece.id, so the status catch-up UPDATE will fail';
  end if;

  -- ... and must NOT have widened into table-wide access, which is the boundary
  -- control-plane-db-gate.py enforces and the reason this migration exists.
  if has_table_privilege('carr_jobs','public.content_piece','select') then
    raise exception '0189 FAILED: carr_jobs gained table-wide select on content_piece';
  end if;
  if has_table_privilege('carr_jobs','public.placement','select') then
    raise exception '0189 FAILED: carr_jobs gained table-wide select on placement';
  end if;
end $$;
