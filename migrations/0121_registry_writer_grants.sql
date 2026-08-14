-- 0121_registry_writer_grants.sql
-- THE SERVICE CATALOG HAD NO WRITER, SO THE REPO DECLARATION HAD NO PATH TO THE
-- DATABASE.
--
-- FOUND BY RUNNING IT, NOT BY READING IT — the same way 0117 was found, and the
-- same rule (5409731b) missed the same way. Eight launchd jobs were added to
-- ops/config/services.json, the change merged green, and
-- `tools/ops-record.py sync-registry` died with `permission denied for table
-- service`. Not for one role: for EVERY role this machine holds.
--
--     owner  -> app_exporter_local   INSERT on ops.service: false
--     write  -> carr_jobs            INSERT on ops.service: false
--     read   -> app_exporter_local   INSERT on ops.service: false
--
-- ops.service, ops.service_environment and ops.service_dependency are owned by
-- neondb_owner and carried exactly one grant between them: SELECT to
-- carr_reader. 0115 created the catalog and granted the tables that unattended
-- collectors write — ops.run and ops.deployment — and stopped there, because at
-- that moment the catalog was being populated by the migration runner itself.
-- So sync-registry has only ever worked as neondb_owner, which no application
-- credential on this Mac is, and the applier that the whole registry design
-- depends on has been unrunnable since the day it was written.
--
-- THE CONSEQUENCE IS QUIETER THAN A CRASH, which is why it survived. A
-- declaration that cannot be applied does not announce itself: services.json
-- keeps parsing, CI keeps passing, `health` keeps printing the rows that were
-- seeded by hand, and the drift only shows up if something compares the file
-- against the table. tools/scheduler-truth.py is what compared them.
--
-- WHY carr_exporter AND NOT carr_jobs. The split 0117 drew is the right one and
-- this migration keeps it. carr_jobs is the unattended collector: it OBSERVES,
-- and observation must never be able to invent the thing it observes. A
-- collector that could insert into ops.service could manufacture a service,
-- give it a cadence, and report it healthy — a machine grading its own homework
-- against a syllabus it wrote. sync-registry is a different act: a human or a
-- session applying a REVIEWED repo declaration that arrived through a pull
-- request. That is the exporter's lane, and app_exporter_local already inherits
-- carr_exporter, so the credential this Mac holds picks it up with no new
-- secret and no change to any connection string.
--
-- NO DELETE, deliberately. sync-registry reconciles by insert-and-update and
-- retires a service by stamping retired_at, never by removing the row — an
-- ops.run pointing at a service_id that no longer exists is an orphan the trace
-- view cannot explain, and history is the reason the catalog exists. Withholding
-- DELETE makes that a property of the database rather than a habit of the
-- writer.

begin;

-- GUARDED ON THE ROLE EXISTING, following 0119's pattern, and not as politeness.
-- The role graph is NOT identical across environments: this migration was
-- written, applied to staging, and died with `role "carr_exporter" does not
-- exist` — staging has never had it, and CI's throwaway Postgres has neither
-- role. An unguarded grant would make this migration un-appliable everywhere
-- except production, which is the one place a schema change should never be
-- proven first. That environment difference is real and worth its own look;
-- this file's job is to be correct in all three, not to hide it.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'carr_exporter') then
    grant select, insert, update on ops.service,
                                    ops.service_environment,
                                    ops.service_dependency
      to carr_exporter;
  else
    raise notice 'carr_exporter absent in this environment — registry grants skipped';
  end if;
end $$;

-- The catalog's surrogate keys come from identity/serial columns, so INSERT
-- alone is not enough: without USAGE on the sequence the insert fails at the
-- default expression rather than at the privilege check, which reads as a
-- puzzling nextval error instead of a permissions one. 0120 learned this on the
-- backup role; recording it here so the next grant migration does not learn it
-- a third time.
do $$
declare s record;
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_exporter') then
    return;
  end if;
  for s in
    select distinct quote_ident(n.nspname) || '.' || quote_ident(c.relname) as seq
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
      join pg_depend d on d.objid = c.oid and d.deptype = 'a'
      join pg_class t on t.oid = d.refobjid
     where c.relkind = 'S'
       and n.nspname = 'ops'
       and t.relname in ('service', 'service_environment', 'service_dependency')
  loop
    execute format('grant usage, select on sequence %s to carr_exporter', s.seq);
  end loop;
end $$;

-- ── proof, because a grant nobody has watched take effect is a hope ──────────
-- The catalog is authoritative about what a grant SAYS. 0117 documents why this
-- cannot be proven by `set role` on this Postgres — SET ROLE needs membership
-- WITH SET, and granting that to make a test pass would widen the role graph
-- for the benefit of an assertion. So this asserts against the catalog, and
-- asserts the withheld privilege too: a grant file that only checks what it
-- added would pass just as happily if it had granted everything.
do $$
declare missing text;
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_exporter') then
    raise notice 'carr_exporter absent — grant proof skipped in this environment';
    return;
  end if;
  select string_agg(t.tbl || '.' || p.priv, ', ')
    into missing
    from (values ('service'), ('service_environment'), ('service_dependency')) as t(tbl)
   cross join (values ('SELECT'), ('INSERT'), ('UPDATE')) as p(priv)
   where not has_table_privilege('carr_exporter', 'ops.' || t.tbl, p.priv);
  if missing is not null then
    raise exception 'registry grant incomplete: %', missing;
  end if;

  if has_table_privilege('carr_exporter', 'ops.service', 'DELETE') then
    raise exception 'carr_exporter must NOT hold DELETE on ops.service — a '
                    'service is retired by stamping retired_at, never removed, '
                    'or every ops.run pointing at it becomes an orphan';
  end if;

  if exists (select 1 from pg_roles where rolname = 'carr_jobs')
     and has_table_privilege('carr_jobs', 'ops.service', 'INSERT') then
    raise exception 'carr_jobs must NOT hold INSERT on ops.service — the '
                    'unattended collector observes services and may never '
                    'invent one';
  end if;
end $$;

commit;
