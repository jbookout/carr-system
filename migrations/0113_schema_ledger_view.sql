-- 0113_schema_ledger_view.sql — /release can finally read its own schema
-- field (Phase 1, closing the gap the endpoint's own first live call found).
--
-- THE DEFECT. mcp-server/src/release.js queries schema_migrations directly
-- over the DATABASE_URL_READER connection (carr_reader). Live response:
-- "database unreachable: permission denied for table schema_migrations".
-- git_sha and doctrine_generation both read correctly — this is one narrow
-- missing grant, not a broken design.
--
-- WHY A VIEW, NOT A DIRECT TABLE GRANT. 0004 built carr_reader as a views-
-- only role on purpose ("carr_reader needs — and gets — ZERO base-table
-- grants"), and that posture is still load-bearing today: mcp.js says so
-- outright ("carr_reader is views-only and cannot INSERT — see 0108's
-- grants") and tools.js's resolveSubject learned it the hard way in
-- production ("views-only is deliberate ... The security model wins; the
-- verb adapts"). Later migrations (0071, 0108, 0111) did grant carr_reader
-- direct SELECT on specific base tables when the table was CARR's own and
-- the full row shape was wanted — but schema_migrations is tools/migrate.py's
-- own bootstrap table (created ad hoc by BOOTSTRAP in tools/migrate.py, not
-- by a tracked migration), and /release only ever needs two facts off it: the
-- highest filename and the count. A view keeps that boundary intact, matches
-- the design 0004 stated, and gives the reader exactly the two columns it
-- needs — never sha256, which is an integrity-check value, not a release fact.
--
-- GRANT SURFACE. v_schema_ledger is new, so its own SELECT grant is the only
-- permission-surface change here (rule 5409731b: a new table or view changes
-- what everything that touches it can see, checked explicitly, not assumed
-- from role membership). carr_reader gets it because that's the role
-- release.js's DATABASE_URL_READER connection uses. carr_exporter is granted
-- explicitly too, belt-and-braces, per 0107's own lesson that carr_exporter's
-- membership in carr_reader has not reliably meant it inherits every reader
-- grant in practice on this schema — even though nothing currently reads this
-- view through carr_exporter. carr_writer and carr_jobs get nothing: neither
-- has a present reader on schema_migrations, and a grant with no caller is
-- exactly the unused surface this rule exists to avoid.

begin;

create view v_schema_ledger as
  select filename, applied_at
    from schema_migrations;

comment on view v_schema_ledger is
  'The /release schema field''s read surface (0113). carr_reader holds no '
  'grant on schema_migrations itself — views-only stays intact — so the '
  'Worker reads highest-applied-migration and applied-count through here.';

grant select on v_schema_ledger to carr_reader, carr_exporter;

do $$
declare n int;
begin
  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_reader'
     and table_name = 'v_schema_ledger'
     and privilege_type = 'SELECT';
  if n <> 1 then
    raise exception '0113: carr_reader missing SELECT on v_schema_ledger';
  end if;

  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_exporter'
     and table_name = 'v_schema_ledger'
     and privilege_type = 'SELECT';
  if n <> 1 then
    raise exception '0113: carr_exporter missing SELECT on v_schema_ledger';
  end if;

  -- the views-only posture must stay intact: this migration grants nothing
  -- on the base table itself.
  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_reader'
     and table_name = 'schema_migrations';
  if n <> 0 then
    raise exception '0113: carr_reader unexpectedly holds a direct grant on schema_migrations — views-only broken';
  end if;
end $$;

commit;
