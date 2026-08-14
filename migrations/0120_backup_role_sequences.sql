-- 0120_backup_role_sequences.sql
--
-- carr_backup can read every table (0119) but pg_dump ALSO reads sequence
-- state (last_value) for every sequence it dumps, and sequences carry their
-- own privilege bit. The first live cloud-backup dispatch on the fixed
-- runner (Actions run 31813750847, 2026-08-14) proved the gap in one line:
--
--   pg_dump: error: failed to get data for sequence "ref_client_seq";
--   user may lack SELECT privilege on the sequence
--
-- 0119's ALTER DEFAULT PRIVILEGES covered future TABLES only — the same
-- future-coverage promise has to be made for sequences separately, which
-- this migration does for both schemas. Same posture as 0119: SELECT and
-- nothing else; a backup role that can bump a sequence is a writer.

grant select on all sequences in schema public to carr_backup;
grant select on all sequences in schema ops to carr_backup;

alter default privileges in schema public grant select on sequences to carr_backup;
alter default privileges in schema ops grant select on sequences to carr_backup;

-- Proof, house style: fail HERE, loudly, rather than at the next 02:35 UTC
-- dispatch. ref_client_seq is the exact sequence the failing run named, so
-- the assertion re-tests the observed failure, not a stand-in. The second
-- block proves full coverage, not one lucky grant.
do $$
begin
  if not has_sequence_privilege('carr_backup', 'public.ref_client_seq', 'SELECT') then
    raise exception '0120 proof failed: carr_backup still cannot SELECT public.ref_client_seq';
  end if;
end $$;

do $$
declare
  missing text;
begin
  select string_agg(schemaname || '.' || sequencename, ', ')
    into missing
    from pg_sequences
   where schemaname in ('public', 'ops')
     and not has_sequence_privilege('carr_backup', quote_ident(schemaname) || '.' || quote_ident(sequencename), 'SELECT');
  if missing is not null then
    raise exception '0120 proof failed: carr_backup cannot SELECT: %', missing;
  end if;
end $$;
