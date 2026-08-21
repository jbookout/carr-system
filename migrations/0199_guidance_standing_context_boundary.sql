-- 0199_guidance_standing_context_boundary.sql
-- Keep Typed Guidance's imported inventory exactly aligned to the corpus that
-- standing-context actually recites. Rules scoped to intro_politics remain
-- active, but intentionally render through their separate introduction surface.
--
-- 0170 is already recorded in the schema ledger, so this is a forward-only
-- replacement rather than an edit to its historical function definitions.

begin;

create or replace function ops.assert_guidance_import_inventory(p_batch_id uuid)
returns void language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
begin
  if not exists (select 1 from ops.guidance_import_batch where id=p_batch_id) then
    raise exception 'unknown guidance import batch %',p_batch_id;
  end if;
  if exists (
    (select id from rule
      where status='active' and coalesce(scope->>'kind','') <> 'intro_politics')
    except
    (select distinct source_rule_id from ops.guidance_import_entry where batch_id=p_batch_id)
  ) or exists (
    (select distinct source_rule_id from ops.guidance_import_entry where batch_id=p_batch_id)
    except
    (select id from rule
      where status='active' and coalesce(scope->>'kind','') <> 'intro_politics')
  ) then
    raise exception 'guidance import batch source inventory no longer exactly matches standing-context active rules';
  end if;
end $$;

create or replace function ops.assert_guidance_registry_coverage()
returns table(source_rule_id uuid, issue text)
language sql stable security definer set search_path=ops,public,pg_temp as $$
  with active_rules as (
    select id from rule
     where status='active' and coalesce(scope->>'kind','') <> 'intro_politics'
  ), primary_counts as (
    select ar.id,
           count(g.*) filter (where g.is_primary) as primary_count
      from active_rules ar
      left join ops.v_guidance_materialized_current g on g.source_rule_id=ar.id
     group by ar.id
  )
  select id,
         case when primary_count=0 then 'missing active primary guidance'
              else 'multiple active primary guidance records' end
    from primary_counts where primary_count <> 1
  union all
  select g.source_rule_id,'constraint lacks admitted installed enforcement projection'
    from ops.v_guidance_materialized_current g
   where g.is_primary and g.guidance_type='constraint'
     and not exists (
       select 1
         from ops.rule_admission a
         join ops.rule_enforcement_point ep
           on ep.rule_id=a.rule_id and ep.installed
        where a.rule_id=g.source_rule_id and a.state='admitted')
  union all
  select g.source_rule_id,'doctrine lacks active WR-AI-006 situation bridge'
    from ops.v_guidance_materialized_current g
   where g.is_primary and g.guidance_type='doctrine'
     and not exists (
       select 1
         from ops.v_guidance_materialized_situation_mapping_current m
         join retrieval_concept c on c.id=m.concept_id and c.status='approved'
         join doctrine_section s on s.id=m.doctrine_section_id and s.status='active'
         join doctrine_concept_mapping dcm
           on dcm.concept_id=m.concept_id
          and dcm.section_id=m.doctrine_section_id
          and dcm.status='approved'
        where m.guidance_revision_id=g.guidance_revision_id and m.state='active')
$$;

commit;

do $$
begin
  if to_regprocedure('ops.assert_guidance_import_inventory(uuid)') is null
     or to_regprocedure('ops.assert_guidance_registry_coverage()') is null then
    raise exception '0199 FAILED: standing-context guidance functions missing';
  end if;
end $$;
