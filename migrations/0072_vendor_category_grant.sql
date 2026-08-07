-- 0072_vendor_category_grant.sql — the grant 0069's whitelist extension needed.
--
-- Found live 2026-08-06 night, first production use of update-vendor's new
-- category_slug branch: the verb pre-validates the slug against vendor_category
-- (so a bad slug returns the valid list instead of a poisoned transaction), but
-- 0050 created that table with owner-only grants — carr_writer cannot SELECT
-- it, so the validation query itself died as an internal error. The branch
-- rehearsal missed this by connecting as neondb_owner (the warm-isolate class:
-- a rehearsal under a stronger role proves nothing about the production role).
-- Precedent for a ref-table writer grant: 0020's party_link_kind.

begin;
grant select on vendor_category to carr_writer;
commit;

do $$
begin
  if not has_table_privilege('carr_writer', 'vendor_category', 'select') then
    raise exception '0072: grant did not land';
  end if;
end $$;
