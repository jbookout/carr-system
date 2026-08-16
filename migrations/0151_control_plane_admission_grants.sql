-- 0151_control_plane_admission_grants.sql
-- The admission verb normalizes captured intake and may revise a proposed
-- contract before activation. Grant those exact updates to carr_writer and no
-- broader authority. A partial unique index serializes the one intake row that
-- belongs to each proposed rule.

begin;

create unique index if not exists guidance_rule_source_once
  on ops.guidance_intake(lane,source_ref)
  where lane='rule' and source_ref like 'rule:%';

grant update on ops.guidance_intake,ops.rule_admission,ops.rule_enforcement_point
  to carr_writer;

commit;

do $$
begin
  if not has_table_privilege('carr_writer','ops.guidance_intake','update')
     or not has_table_privilege('carr_writer','ops.rule_admission','update')
     or not has_table_privilege('carr_writer','ops.rule_enforcement_point','update') then
    raise exception '0151 FAILED: carr_writer cannot complete admission';
  end if;
end $$;
