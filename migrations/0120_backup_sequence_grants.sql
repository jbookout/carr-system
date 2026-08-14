-- 0120_backup_sequence_grants.sql
--
-- The first live GitHub backup using carr_backup reached the CARR schemas but
-- failed on ref_client_seq: PostgreSQL sequence privileges are independent of
-- table privileges, and 0119 granted only tables.  pg_dump reads each
-- sequence's current value so a schema-and-data backup needs SELECT on every
-- current and future CARR sequence as well.
--
-- SELECT is the narrow privilege.  carr_backup does not receive USAGE
-- (currval/nextval) or UPDATE (setval/nextval), remains unable to mutate any
-- sequence, and still has no access to Neon-managed schemas.

begin;

grant select on all sequences in schema public to carr_backup;
grant select on all sequences in schema ops    to carr_backup;

alter default privileges in schema public grant select on sequences to carr_backup;
alter default privileges in schema ops    grant select on sequences to carr_backup;

commit;

-- Guards run in production and in CI's throwaway database.  The first proves
-- the repair landed; the second keeps later permission edits from broadening
-- or silently dropping the backup contract.
do $$
declare n int; missing text; excess text;
begin
  select string_agg(format('%I.%I', schemaname, sequencename), ', ')
    into missing
    from pg_sequences
   where schemaname in ('public', 'ops')
     and not has_sequence_privilege(
       'carr_backup', format('%I.%I', schemaname, sequencename), 'SELECT');
  if missing is not null then
    raise exception 'carr_backup cannot SELECT sequence(s): %', missing;
  end if;

  select string_agg(format('%I.%I', schemaname, sequencename), ', ')
    into excess
    from pg_sequences
   where schemaname in ('public', 'ops')
     and (has_sequence_privilege(
            'carr_backup', format('%I.%I', schemaname, sequencename), 'USAGE')
       or has_sequence_privilege(
            'carr_backup', format('%I.%I', schemaname, sequencename), 'UPDATE'));
  if excess is not null then
    raise exception 'carr_backup has mutable sequence privilege(s): %', excess;
  end if;

  select count(distinct ns.nspname) into n
    from pg_default_acl da
    join pg_namespace ns on ns.oid = da.defaclnamespace
   where ns.nspname in ('public', 'ops')
     and da.defaclobjtype = 'S'
     and exists (
       select 1
         from aclexplode(da.defaclacl) x
         join pg_roles gr on gr.oid = x.grantee
        where gr.rolname = 'carr_backup'
          and x.privilege_type = 'SELECT'
     );
  if n <> 2 then
    raise exception 'carr_backup sequence default-privilege coverage = % '
                    '(expected 2: public + ops)', n;
  end if;

  raise notice 'carr_backup: SELECT on every current sequence in public+ops, '
               'default SELECT on future sequences in both, no USAGE/UPDATE';
end $$;
