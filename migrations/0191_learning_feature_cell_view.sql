-- 0191_learning_feature_cell_view.sql
-- The third and last consumer stranded by the control-plane read boundary.
-- 0189 and 0190 fixed pipelines/pull_placement_metrics.py, which CREATES the
-- rows. pipelines/learning_jobs.py READS them to build the (platform x format)
-- feature cells the weekly learning job is entirely about, and it still joins
-- placement to content_piece directly. Under carr_jobs that read returns
-- nothing, so the job has been printing
--   "the placement records could not be read under this credential"
-- on every run. That output is honest and it is not a count of zero, which is
-- exactly why it went unnoticed: a real below-threshold week reads almost the
-- same. Confirmed still printing on 2026-08-20 after the pull itself was fixed.
--
-- Same shape as 0189's projections and the same reason: a definer view over the
-- join, no PII. It carries the platform, the format label off the piece's own
-- features, the placement's surrogate id and live timestamp, and a count of
-- metric rows. `format` stays coalesced to 'unknown' inside the view so every
-- caller bins the same way and no consumer can invent a different default.

begin;

create or replace view public.v_control_plane_social_feature_cells as
select p.platform,
       coalesce(cp.features->>'format', 'unknown') as format,
       p.id as placement_id,
       p.live_at,
       (select count(*) from public.placement_metric m where m.placement_id = p.id)
         as metric_rows
  from public.placement p
  join public.content_piece cp on cp.id = p.piece_id;

grant select on public.v_control_plane_social_feature_cells to carr_jobs;

commit;

do $$
begin
  if not has_table_privilege('carr_jobs','public.v_control_plane_social_feature_cells','select') then
    raise exception '0191 FAILED: carr_jobs cannot read the feature-cell projection';
  end if;

  -- the boundary control-plane-db-gate.py enforces must be untouched
  if has_table_privilege('carr_jobs','public.placement','select')
     or has_table_privilege('carr_jobs','public.content_piece','select') then
    raise exception '0191 FAILED: carr_jobs gained table-wide select on a guarded source table';
  end if;
end $$;
