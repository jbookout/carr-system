-- staging-isolation-proof.sql — the denial proof gate G1 asks for.
--
-- G1's requirement is not "a staging environment exists". It is that staging
-- CANNOT REACH PRODUCTION DATA OR CREDENTIALS, and that this is demonstrated
-- rather than asserted. A config file claiming isolation proves nothing; the
-- interesting failure is a staging binding quietly pointed at production, which
-- looks exactly like a working staging environment until it destroys something.
--
-- Run it against staging:
--   .venv/bin/python tools/db-tap.py --project staging sql pipelines/staging-isolation-proof.sql
--
-- Every check RAISES rather than returning a row, so a failure cannot be
-- mistaken for output nobody read.
--
-- The `\set ON_ERROR_STOP on` line that used to sit here was a psql meta-command,
-- and db-tap's sql mode no longer runs psql (see _run_sql_in_process). It was
-- already redundant when it was written — db-tap passed -v ON_ERROR_STOP=1 on the
-- command line — and stopping on the first error is now structural rather than
-- requested: the whole script runs inside one transaction that rolls back.

-- 1. It is a real, fully built database, not an empty shell that would pass the
--    emptiness checks below for the wrong reason.
do $$
declare n int;
begin
  select count(*) into n from information_schema.tables
   where table_schema = 'public' and table_type = 'BASE TABLE';
  if n < 100 then
    raise exception 'staging has only % public tables — the schema did not load', n;
  end if;
  raise notice 'staging schema: % tables', n;
end $$;

-- 2. It carries the applied-migration ledger, so the runner knows what is
--    already in place and only applies genuinely new migrations.
do $$
declare n int;
begin
  select count(*) into n from schema_migrations;
  if n < 100 then
    raise exception 'staging ledger has only % rows — schema.sql did not carry it', n;
  end if;
  raise notice 'staging ledger: % migrations recorded as applied', n;
end $$;

-- 3. THE ISOLATION PROOF ITSELF. Not one production business row is present.
--    These are the tables that would hold real clients, deals and people. If any
--    is non-empty, this database is not what it claims to be — either the
--    snapshot leaked data or the connection is pointed somewhere else entirely.
do $$
declare
  t text;
  n int;
  offenders text := '';
begin
  foreach t in array array['client','deal','party','client_note','deal_event']
  loop
    if to_regclass('public.' || t) is null then
      continue;  -- table not in this schema version; not a leak
    end if;
    execute format('select count(*) from public.%I', t) into n;
    if n > 0 then
      offenders := offenders || format('%s=%s ', t, n);
    end if;
  end loop;
  if offenders <> '' then
    raise exception 'NOT ISOLATED: staging holds production business rows: %', offenders;
  end if;
  raise notice 'isolation: no business rows in client/deal/party/notes/events';
end $$;

-- 4. The reference vocabulary MUST be present, and this ASSERTS rather than
--    reports. The first staging build passed every isolation check above and was
--    still useless: 115 tables and ZERO client statuses, because a schema-only
--    dump carries structure and not the closed vocabularies the foreign keys
--    point at. client.status references client_status, so an empty vocabulary
--    means the database cannot hold a single row. Reporting that as a friendly
--    notice is exactly how it went unnoticed the first time.
do $$
declare t text; n int; empty text := '';
begin
  foreach t in array array['client_status','client_type','deal_phase','lead_stage',
                           'vendor_category','participant_role','actor']
  loop
    if to_regclass('public.' || t) is null then
      empty := empty || t || '(missing) '; continue;
    end if;
    execute format('select count(*) from public.%I', t) into n;
    if n = 0 then empty := empty || t || '(0) '; end if;
  end loop;
  if empty <> '' then
    raise exception 'UNUSABLE: reference vocabulary empty for: % — staging has structure but nothing can be inserted', empty;
  end if;
  raise notice 'reference vocabulary: present and non-empty';
end $$;

select 'staging isolation proof: PASSED' as result;
