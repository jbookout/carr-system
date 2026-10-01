-- Catalog proof over every definer, including routines Dot cannot execute.
\set ON_ERROR_STOP on
begin;
set local search_path = pg_catalog;
do $paths$
declare missing_count integer; examples text;
begin
  select count(*), string_agg(signature, ', ' order by signature)
    into missing_count, examples
    from (
      select p.oid::regprocedure::text signature
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace
       where n.nspname in ('public','ops') and p.prosecdef
         and not exists (
           select 1 from unnest(p.proconfig) setting
            where setting like 'search_path=%'
              and (regexp_split_to_array(substring(setting from 13), '\s*,\s*'))[
                cardinality(regexp_split_to_array(substring(setting from 13), '\s*,\s*'))
              ] = 'pg_temp'
         )
    ) missing;
  if missing_count > 0 then
    raise exception '% SECURITY DEFINER search paths do not end in pg_temp: %',
      missing_count, left(examples, 1000);
  end if;
end $paths$;
rollback;
