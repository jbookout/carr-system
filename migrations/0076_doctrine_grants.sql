-- 0076_doctrine_grants.sql — the grants 0075 forgot, caught live within the
-- hour: the deployed Worker's first doctrine-index call returned internal
-- error while the branch rehearsal (as neondb_owner) had been green. Same
-- lesson 0019/0020 already recorded: "all tables" grants bind at grant time,
-- so every migration that creates a table owes its grants in the same file.
-- 0075 didn't, and the owner-role rehearsal was structurally unable to catch
-- it — a rehearsal blind spot worth remembering: REHEARSE AS THE ROLE THAT
-- WILL RUN IT.

begin;

grant select, insert, update on
  doctrine_document, doctrine_slug_alias, doctrine_section, doctrine_revision,
  doctrine_change_set, doctrine_change_item, doctrine_link, doctrine_edge,
  doctrine_review_policy, doctrine_meta, doctrine_snapshot, doctrine_claim,
  doctrine_gate_check, doctrine_gate_run, doctrine_gate_finding,
  doctrine_migration_batch
to carr_writer;

-- DELETE is granted on exactly TWO tables, each with its reason, keeping the
-- house posture (writers never delete records) intact everywhere else:
--   doctrine_link  — set-doctrine-refs REPLACES a section's outbound links;
--                    links are derived annotations, not records (the edge
--                    table, which carries semantics, retires rows instead).
--   doctrine_claim — claims are expiring coordination hints, not records;
--                    claim_release removes its own row.
grant delete on doctrine_link, doctrine_claim to carr_writer;

grant select on
  doctrine_document, doctrine_slug_alias, doctrine_section, doctrine_revision,
  doctrine_edge_type, doctrine_edge, doctrine_link, doctrine_review_policy,
  doctrine_meta, doctrine_snapshot, doctrine_gate_check
to carr_reader;

-- edge_type is reference vocab: writers read it, nobody but owner writes it.
grant select on doctrine_edge_type to carr_writer;

-- The 0019 pattern: prove the grants landed, fail the migration if not.
do $$
declare n int;
begin
  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_writer' and table_schema = 'public'
     and table_name like 'doctrine\_%' and privilege_type = 'SELECT';
  if n < 17 then
    raise exception 'carr_writer doctrine grants incomplete (% of 17 SELECTs)', n;
  end if;
end $$;

commit;
