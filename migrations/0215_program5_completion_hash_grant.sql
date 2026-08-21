-- 0215_program5_completion_hash_grant.sql
-- Program 5: let the routine release writer execute the deterministic hash
-- helper reached by its invoker-rights completion trigger.
--
-- 0202 made release_completion_requires_a_read_back() compare the exact
-- recovery bundle migration set by calling this helper.  That trigger runs as
-- the role updating ops.release.  carr_jobs has the deliberately narrow
-- release UPDATE and trigger-read grants from 0133, but 0202 revoked PUBLIC
-- EXECUTE and never granted this final, pure helper capability.  Completion
-- therefore failed after all evidence checks with permission denied.

begin;

-- Keep the helper private to the one routine role whose release-completion
-- trigger invokes it.  The function is immutable, accepts only its supplied
-- migration-set array, and cannot mutate release or evidence state.
revoke all on function ops.program5_migration_set_sha256(text[])
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.program5_migration_set_sha256(text[]) to carr_jobs;

do $$
begin
  if not has_function_privilege(
    'carr_jobs','ops.program5_migration_set_sha256(text[])','execute') then
    raise exception '0215 FAILED: carr_jobs cannot execute the completion-trigger hash helper';
  end if;
  if has_function_privilege(
    'carr_reader','ops.program5_migration_set_sha256(text[])','execute')
     or has_function_privilege(
       'carr_writer','ops.program5_migration_set_sha256(text[])','execute')
     or has_function_privilege(
       'carr_authority','ops.program5_migration_set_sha256(text[])','execute') then
    raise exception '0215 FAILED: Program 5 completion hash helper escaped carr_jobs';
  end if;
end $$;

commit;
