-- 0095_vendor_lookup_grants.sql — the two grants 0072 left behind.
--
-- 0072 fixed vendor_category after the missing grant took down update-vendor's
-- first production use: the verb pre-validates a slug against its ref table, but
-- the table had owner-only grants, so the validation query itself died as an
-- internal error under carr_writer. Measured 2026-08-11: vendor_disposition and
-- vendor_relationship_level are still in exactly that state.
--
--   select has_table_privilege('carr_writer', t, 'select')
--     -> vendor_stage t · vendor_category t · vendor_disposition f
--        · vendor_relationship_level f
--
-- Both are live foreign-key targets on vendor (vendor_disposition_fkey,
-- vendor_relationship_level_fkey), so this is latent rather than broken: nothing
-- on the write path reads them today. The next verb that validates against
-- either one dies the same unexplained way 0072 had to fix — and it dies during
-- its first production use, not during a rehearsal, because a rehearsal under
-- neondb_owner proves nothing about the production role (the warm-isolate class
-- 0072 already names).
--
-- Granted now, ahead of the verb, so the ordering that produced 0072 cannot
-- repeat. Precedent for a ref-table writer grant: 0020's party_link_kind, 0072's
-- vendor_category.

begin;
grant select on vendor_disposition to carr_writer;
grant select on vendor_relationship_level to carr_writer;
commit;

do $$
begin
  if not has_table_privilege('carr_writer', 'vendor_disposition', 'select') then
    raise exception '0095: vendor_disposition grant did not land';
  end if;
  if not has_table_privilege('carr_writer', 'vendor_relationship_level', 'select') then
    raise exception '0095: vendor_relationship_level grant did not land';
  end if;
end $$;
