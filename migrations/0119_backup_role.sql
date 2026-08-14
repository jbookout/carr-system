-- 0119_backup_role.sql
-- PROGRAM 4, THE OFF-MAC COPY: a dedicated, read-only role for GitHub Actions'
-- nightly pg_dump, so the encrypted backup no longer depends on Joe's Mac
-- being on, awake, and reachable (Joe-approved, 2026-08-14).
--
-- WHY A FOURTH ROLE RATHER THAN REUSING carr_reader. carr_reader is
-- views-only by construction (0004: "carr_reader gets view grants only,
-- zero base-table grants") — that is the whole point of the reader bundle,
-- and pg_dump needs the base tables: a schema-and-data dump of a handful of
-- views is not a backup, it is a sample. carr_jobs is column-scoped to the
-- exact statements two pipelines run (0021) and holds INSERT/UPDATE on
-- three tables — the wrong shape twice over for a role whose only job is to
-- leave with a full read-only copy once a night. Neither existing role
-- fits, so this is a new one, held to the same NOLOGIN-until-Joe password
-- discipline 0021 established for carr_jobs.
--
-- SCOPE, verbatim from the brief: SELECT on every table in every schema,
-- now and later, and nothing else — no INSERT, no UPDATE, no DELETE, no DDL.
--
-- ONE DELIBERATE DEPARTURE FROM THE 0023 CONVENTION, FLAGGED RATHER THAN
-- BURIED. 0023 established that `grant ... on all tables in schema` is a
-- ONE-TIME grant, not a standing rule, and every table born after 0004 gets
-- an explicit per-table grant at creation (rule 5409731b: a new table
-- changes the permission surface, grant-check it). That convention exists
-- because carr_writer/carr_reader/carr_jobs are least-privilege bundles
-- where "does this role need THIS table" is a real question with a real
-- no answer sometimes. For a backup role the question has exactly one
-- answer, always: yes, every table — because a backup that silently
-- excludes whatever was added after this role was created is not a backup,
-- it is a gap nobody notices until a restore needs the missing table.
-- ALTER DEFAULT PRIVILEGES is the correct tool for exactly this shape of
-- requirement, so it is used here and nowhere else in this schema.
-- LIMITATION, stated rather than hidden: default privileges cover future
-- TABLES in the schemas named below, not a future SCHEMA. A schema born
-- after this migration (the way `ops` was born in 0114) needs one line in
-- the migration that creates it — `grant usage on schema X to carr_backup;
-- grant select on all tables in schema X to carr_backup;` — the same line
-- 0114 already adds for carr_reader/carr_writer/carr_jobs. Nothing here can
-- reach forward past a schema that does not exist yet.
--
-- THE PASSWORD IS NOT IN THIS FILE AND NOBODY HAS EVER SEEN IT, same
-- mechanism as 0021's carr_jobs: a random placeholder generated inside the
-- transaction, unusable and unrecorded. Joe runs
--     alter role carr_backup password '<his own value>';
-- and lands it as the GitHub Actions secret BACKUP_DATABASE_URL. The
-- executing agent never holds the value at any point in that sequence.
--
-- Purpose: GitHub-Actions pg_dump only. Provisioned 2026-08-14, Joe-approved
-- (Program 4). See .github/workflows/backup-nightly.yml.

begin;

-- ── 1. the role ──────────────────────────────────────────────────────────────
do $$
declare pw text := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
begin
  if exists (select 1 from pg_roles where rolname = 'carr_backup') then
    raise notice 'carr_backup already exists — password left exactly as it is';
  else
    execute format('create role carr_backup login password %L', pw);
    raise notice 'carr_backup created with a random placeholder password that is '
                 'not recorded anywhere. It cannot be used until Joe runs '
                 'alter role carr_backup password ''<his value>''.';
  end if;
end $$;

comment on role carr_backup is
  'GitHub Actions'' nightly pg_dump role only (Program 4, 2026-08-14, '
  'Joe-approved). SELECT on every table in every schema, now and future '
  '(ALTER DEFAULT PRIVILEGES) — the one role in this schema that '
  'deliberately does not follow the per-table grant convention, because a '
  'backup that misses a table added after this migration is not a backup. '
  'NO INSERT/UPDATE/DELETE/DDL anywhere, ever. Held through '
  'BACKUP_DATABASE_URL, a GitHub Actions secret, never on Joe''s Mac and '
  'never in this repo. See .github/workflows/backup-nightly.yml.';

-- ── 2. connect + schema usage ────────────────────────────────────────────────
do $$
begin
  execute format('grant connect on database %I to carr_backup', current_database());
end $$;

grant usage on schema public to carr_backup;
grant usage on schema ops    to carr_backup;

-- ── 3. read every table that exists today ────────────────────────────────────
grant select on all tables in schema public to carr_backup;
grant select on all tables in schema ops    to carr_backup;

-- ── 4. read every table born after today, automatically ─────────────────────
-- Scoped to objects created by whichever role executes this statement — in
-- every environment that is the single admin credential migrations always
-- run as (neondb_owner in production, per 0005; the CI bootstrap role in
-- CI), and it is the same role every later migration's CREATE TABLE runs
-- as, so no FOR ROLE clause is needed. None is added on purpose: naming a
-- role here would silently stop covering new tables if migrations were ever
-- run as anyone else, which is the opposite of the guarantee this migration
-- exists to make.
alter default privileges in schema public grant select on tables to carr_backup;
alter default privileges in schema ops    grant select on tables to carr_backup;

commit;

-- ── 5. guards ────────────────────────────────────────────────────────────────
do $$
declare n int; missing text;
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_backup' and rolcanlogin) then
    raise exception 'carr_backup is missing or cannot log in';
  end if;

  -- no createdb, no superuser, no createrole, no replication — a dump
  -- credential is not an admin credential.
  if exists (select 1 from pg_roles
              where rolname = 'carr_backup'
                and (rolsuper or rolcreatedb or rolcreaterole or rolreplication)) then
    raise exception 'carr_backup holds an elevated role attribute — a backup credential must be plain login only';
  end if;

  -- it must not have inherited a bundle: any of the app roles would make
  -- the least-privilege story here decoration.
  if exists (select 1 from pg_auth_members m
               join pg_roles r on r.oid = m.roleid
               join pg_roles g on g.oid = m.member
              where g.rolname = 'carr_backup'
                and r.rolname in ('carr_writer','carr_reader','carr_jobs','carr_exporter')) then
    raise exception 'carr_backup is a member of a privilege bundle — a dump role must not inherit write access';
  end if;

  -- zero write or DDL-adjacent privileges anywhere, on anything, ever.
  select count(*) into n
    from information_schema.role_table_grants
   where grantee = 'carr_backup'
     and privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER');
  if n <> 0 then
    raise exception 'carr_backup holds % write/DDL-adjacent grant(s) — expected 0', n;
  end if;

  -- every base table in public and ops is readable today.
  select string_agg(table_schema || '.' || table_name, ', ') into missing
    from information_schema.tables t
   where t.table_schema in ('public','ops') and t.table_type = 'BASE TABLE'
     and not has_table_privilege('carr_backup', t.table_schema || '.' || t.table_name, 'SELECT');
  if missing is not null then
    raise exception 'carr_backup cannot SELECT: %', missing;
  end if;

  -- future tables are covered too: a default ACL exists for both schemas,
  -- naming carr_backup, granting SELECT.
  select count(*) into n
    from pg_default_acl da
    join pg_namespace ns on ns.oid = da.defaclnamespace
   where ns.nspname in ('public','ops')
     and da.defaclobjtype = 'r'
     and exists (
       select 1 from aclexplode(da.defaclacl) x
       join pg_roles gr on gr.oid = x.grantee
      where gr.rolname = 'carr_backup' and x.privilege_type = 'SELECT'
     );
  if n <> 2 then
    raise exception 'carr_backup default-privilege coverage = % (expected 2: public + ops)', n;
  end if;

  raise notice 'carr_backup: can log in, no elevated attributes, no bundle '
               'membership, 0 write/DDL grants, SELECT on every current '
               'table in public+ops, default-privilege coverage for future '
               'tables in both';
end $$;
