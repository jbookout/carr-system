-- 0159_control_plane_evidence_grants.sql
-- The job runner builds typed inputs and predicates from canonical read
-- surfaces. Grant those reads explicitly; it still receives no owner role and
-- no direct write privilege on business records.

begin;

grant select on v_expired_verification,candidate_pool,content_piece,placement
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
      raise exception '0159 FAILED: carr_jobs cannot read %',relation;
    end if;
  end loop;
end $$;
