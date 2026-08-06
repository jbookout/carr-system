-- rebless-0001.sql — one-time production rebless of 0001_init.sql's recorded sha.
-- WHY: ORDER 42b (2026-08-06) sanitized PII in comments/literals of 0001_init.sql
-- AFTER it was applied; migrate.py's drift check correctly refuses the next apply.
-- The change is textual, never schema. This pins the RECORDED sha to the
-- sanitized file's hash, computed at authoring time and guarded below.
-- Run once via db-tap (Joe's tap), before the 0069 production apply.
begin;
update schema_migrations
   set sha256 = '877284179e1bd77fd6f008b1c764016f5a43218267256efa061a6cf700df5a48'
 where filename = '0001_init.sql'
   and sha256 <> '877284179e1bd77fd6f008b1c764016f5a43218267256efa061a6cf700df5a48';
do $$
declare n int;
begin
  select count(*) into n from schema_migrations
   where filename = '0001_init.sql' and sha256 = '877284179e1bd77fd6f008b1c764016f5a43218267256efa061a6cf700df5a48';
  if n <> 1 then
    raise exception 'rebless failed: expected exactly 1 blessed row, found % — rolled back', n;
  end if;
end $$;
commit;
