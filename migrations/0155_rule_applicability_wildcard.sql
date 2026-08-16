-- 0155_rule_applicability_wildcard.sql
-- The Phase 1 bridge uses '*' as an explicit all-workflows/surfaces/tiers tag.
-- Make the policy compiler interpret that finite sentinel as a wildcard.

begin;

create or replace function ops.applicable_rules(
  p_workflow text default null,p_surface text default null,p_tier text default null
) returns table(rule_id uuid,statement text,enforcement_class text,binding_moment text,applicability jsonb)
language sql stable
as $$
  select r.id,r.statement,a.enforcement_class,a.binding_moment,a.applicability
    from rule r join ops.rule_admission a on a.rule_id=r.id
   where r.status='active' and a.state='admitted'
     and (p_workflow is null or not(a.applicability?'workflows')
          or a.applicability->'workflows'?'*' or a.applicability->'workflows'?p_workflow)
     and (p_surface is null or not(a.applicability?'surfaces')
          or a.applicability->'surfaces'?'*' or a.applicability->'surfaces'?p_surface)
     and (p_tier is null or not(a.applicability?'tiers')
          or a.applicability->'tiers'?'*' or a.applicability->'tiers'?p_tier)
   order by r.created_at,r.id
$$;

grant execute on function ops.applicable_rules(text,text,text) to carr_reader,carr_writer,carr_jobs;

commit;
