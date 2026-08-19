-- 0189_restore_jobs_content_reads.sql
-- The weekly metrics pull died on 2026-08-19 with
--   psycopg.errors.InsufficientPrivilege: permission denied for table placement
-- raised from pipelines/pull_placement_metrics.py:290. Under CARR_DB_JOBS_URL the
-- role carr_jobs held INSERT and UPDATE on `placement` and `content_piece` but not
-- SELECT, while `placement_metric` still held all three.
--
-- 0021 granted select+insert+update on all three of those tables together, and
-- 0159 granted select on content_piece and placement again (plus candidate_pool
-- and v_expired_verification) with a do-block that raises if any is unreadable.
-- Production reports 0159 APPLIED, and all four were nonetheless unreadable on
-- 2026-08-19. The same pull ran APPLIED over 122 pieces on 2026-08-12, so the
-- privilege was present then and absent a week later. Nothing in this repo
-- revokes it: the only revokes naming carr_jobs are 0167 and 0175, and neither
-- touches these tables. The cause of the loss is NOT yet identified — see the
-- open loop on this. This migration restores the grant so the pull runs again;
-- it deliberately does not claim to fix whatever removed it.
--
-- Idempotent and additive: re-granting a privilege the role already holds is a
-- no-op, so this is safe to re-run if the loss recurs.

begin;

grant select on v_expired_verification, candidate_pool, content_piece, placement
  to carr_jobs;

commit;

do $$
declare relation text;
begin
  foreach relation in array array[
    'public.v_expired_verification','public.candidate_pool',
    'public.content_piece','public.placement'
  ] loop
    if not has_table_privilege('carr_jobs',relation,'select') then
      raise exception '0189 FAILED: carr_jobs cannot read %',relation;
    end if;
  end loop;
end $$;
