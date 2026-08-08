-- 0078_exporter_doctrine_select.sql — the fourth runtime-role catch of the
-- build (after 0076 carr_writer, 0077 carr_reader actor columns, and the
-- import door's app_writer choice): retrieve's new store pass runs on the
-- EXPORTER credential (lib/record_sources), and carr_exporter had no eyes on
-- the doctrine tables — caught live by the fail-soft line on the store pass's
-- very first production run ("store pass skipped (InsufficientPrivilege)").
--
-- Read-only by construction: the exporter renders and retrieves, never writes
-- doctrine. The write path stays the connector's alone.

begin;

grant select on
  doctrine_document, doctrine_section, doctrine_revision, doctrine_edge,
  doctrine_edge_type, doctrine_link, doctrine_meta, doctrine_snapshot,
  doctrine_migration_batch, doctrine_review_policy, doctrine_gate_check,
  doctrine_gate_run, doctrine_gate_finding
to carr_exporter;

do $$
declare n int;
begin
  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_exporter' and table_schema = 'public'
     and table_name like 'doctrine\_%' and privilege_type = 'SELECT';
  if n < 13 then
    raise exception 'carr_exporter doctrine grants incomplete (% of 13)', n;
  end if;
end $$;

commit;
