-- 0156_control_plane_input_grants.sql
-- The jobs-role input builder needs the safe loop projection for idea and
-- actionable-loop evidence. It receives the view only, never loop base tables.

begin;
grant select on public.v_loops to carr_jobs;
commit;

do $$
begin
  if not has_table_privilege('carr_jobs','public.v_loops','select') then
    raise exception '0156 FAILED: jobs role cannot build loop/idea cognition input';
  end if;
end $$;
