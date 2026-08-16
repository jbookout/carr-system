-- 0152_rule_writer_grants.sql
-- The Phase 1 firing-role gate found that isolated staging's carr_writer bundle
-- could read the new admission contract but could not perform the rule insert
-- or activation whose trigger consumes it. Reassert the narrow rule-table
-- grants explicitly; do not rely on 0004's historical one-time ALL TABLES grant.

begin;

grant select,insert,update on public.rule to carr_writer;

commit;

do $$
begin
  if not has_table_privilege('carr_writer','public.rule','select')
     or not has_table_privilege('carr_writer','public.rule','insert')
     or not has_table_privilege('carr_writer','public.rule','update') then
    raise exception '0152 FAILED: carr_writer cannot capture and activate rules';
  end if;
  if has_table_privilege('carr_writer','public.rule','delete') then
    raise exception '0152 FAILED: carr_writer gained rule deletion';
  end if;
end $$;
