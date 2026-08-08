-- 0080_reader_briefing_grants.sql — the standing-context verb (the #219
-- session-briefing design) serves rules + action-required on the READER path,
-- so carr_reader needs the compiled-rules view and the loop tables it briefs
-- from. Loop content is operational, not sensitive; the views-only posture
-- stays for everything else.

begin;

grant select on v_compiled_rules to carr_reader;
grant select on loop_item, loop_block, loop_domain to carr_reader;

do $$
declare n int;
begin
  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_reader'
     and table_name in ('v_compiled_rules','loop_item','loop_block','loop_domain')
     and privilege_type = 'SELECT';
  if n < 4 then
    raise exception 'briefing grants incomplete (% of 4)', n;
  end if;
end $$;

commit;
