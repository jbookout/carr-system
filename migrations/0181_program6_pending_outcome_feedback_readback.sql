-- Program 6: safely recover the latest unaccepted outcome-feedback proposal
-- after a browser reload.  This is a separate read projection: accepted-only
-- card history remains accepted-only and no read can imply completion.

begin;

create or replace function ops.pending_sourced_work_request_outcome_feedback(
  p_work_request text, p_organization_tenant_id text
)
returns table (
  ref text, state text, version integer,
  plan_ref text, plan_hash text,
  feedback_ref text, feedback_hash text, outcome text,
  criterion_results jsonb, evidence_refs jsonb, blocker_code text,
  result_summary text, observed_minutes integer, interaction_surface text,
  heavy_session_used boolean, manual_context_transfers integer,
  proposed_at timestamptz, status text
)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select w.ref,w.state,w.version,
         p.plan_ref,p.plan_hash,
         f.feedback_ref,f.feedback_hash,f.outcome,
         f.criterion_results,f.evidence_refs,f.blocker_code,
         f.result_summary,f.observed_minutes,f.interaction_surface,
         f.heavy_session_used,f.manual_context_transfers,
         f.created_at,'pending_human_acceptance'::text
    from ops.work_request w
    join public.doctrine_section s on s.id=w.doctrine_section_id
    join public.doctrine_document d on d.id=s.document_id
    join ops.sourced_work_request_outcome_feedback f
      on f.work_request_id=w.id
     and f.work_request_version=w.version
    join ops.sourced_work_request_plan p
      on p.id=f.plan_id
     and p.work_request_id=w.id
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.id=f.plan_acceptance_receipt_id
     and ar.work_request_id=w.id
     and ar.plan_id=p.id
     and ar.result_version=w.version
     and ar.plan_hash=p.plan_hash
    left join ops.sourced_work_request_outcome_feedback_acceptance_receipt accepted
      on accepted.feedback_id=f.id
   where p_organization_tenant_id='carr-internal'
     and w.organization_tenant_id='carr-internal'
     and w.ref=p_work_request
     and w.state='ready'
     and d.visibility='shared'
     and accepted.id is null
   order by f.feedback_version desc
   limit 1;
$$;

comment on function ops.pending_sourced_work_request_outcome_feedback(text,text) is
  'Safe readback of only the latest still-unaccepted Program 6 outcome-feedback proposal for one exact current ready Work Request and accepted plan. It is separate from accepted-only card history and never claims execution, completion, success, approval, release, or displacement.';

revoke all on function ops.pending_sourced_work_request_outcome_feedback(text,text)
  from public;
grant execute on function ops.pending_sourced_work_request_outcome_feedback(text,text)
  to carr_reader,carr_writer;

commit;
