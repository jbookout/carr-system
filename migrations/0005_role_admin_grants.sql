-- 0005: neondb_owner (the admin/migration role; NOT superuser on Neon) gets
-- membership in both privilege bundles so it can SET ROLE for testing and
-- so future login roles can be granted a bundle by the migration runner.
grant carr_reader to neondb_owner;
grant carr_writer to neondb_owner;
