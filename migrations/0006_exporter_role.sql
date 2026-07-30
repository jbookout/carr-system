-- 0006: the exporter privilege bundle. Exporters read the export views and
-- write exactly ONE thing: their own audit row. They also read export_run
-- (for the last-ok tolerance check) and system_config (for thresholds).
create role carr_exporter nologin;
grant usage on schema public to carr_exporter;
grant carr_reader to carr_exporter;                 -- all view SELECTs
grant select, insert on export_run to carr_exporter;
grant select on system_config to carr_exporter;
grant carr_exporter to neondb_owner;
