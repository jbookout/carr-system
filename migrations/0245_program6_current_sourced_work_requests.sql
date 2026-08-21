-- Program 6 first use: a bounded, typed collection of actionable sourced Work
-- Requests. It is read-only; callers cannot choose its tenant, source, state,
-- order, or raw database identifiers.

begin;

create or replace function ops.current_sourced_work_requests(
  p_organization_tenant_id text
)
returns table (
  ref text,
  title text,
  state text,
  source_label text,
  source_freshness text,
  next_human_action text
)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select w.ref,
         w.title,
         w.state,
         coalesce(s.title, s.section_key) as source_label,
         'current'::text as source_freshness,
         case
           when w.state = 'captured' then 'Review and triage'
           when w.state = 'triaged' then 'Prepare or review bounded plan'
           when w.state = 'ready' then 'Record or review outcome evidence'
         end as next_human_action
    from ops.work_request w
    join public.doctrine_section s on s.id = w.doctrine_section_id
    join public.doctrine_document d on d.id = s.document_id
   where p_organization_tenant_id = 'carr-internal'
     and w.organization_tenant_id = p_organization_tenant_id
     and w.capture_idempotency_key is not null
     and w.state in ('captured', 'triaged', 'ready')
     and d.visibility = 'shared'
     and s.status = 'active'
     and s.current_revision_id = w.doctrine_revision_id
     and (
       w.state <> 'triaged'
       or (
         not exists (
           select 1
             from ops.sourced_work_request_plan existing_plan
            where existing_plan.work_request_id = w.id
         )
         and exists (
           select 1
             from public.doctrine_document runbook_document
             join public.doctrine_section runbook_section
               on runbook_section.document_id = runbook_document.id
             join public.doctrine_revision runbook_revision
               on runbook_revision.id = runbook_section.current_revision_id
              and runbook_revision.section_id = runbook_section.id
            where runbook_document.slug = 'runbook'
              and runbook_document.visibility = 'shared'
              and runbook_section.section_key = 'diagnosis-checklist-in-order-2-minutes'
              and runbook_section.status = 'active'
              and runbook_revision.content_hash ~ '^[0-9a-f]{64}$'
              and encode(public.digest(runbook_revision.plain_text, 'sha256'), 'hex') = runbook_revision.content_hash
              and runbook_revision.body = jsonb_build_object('text', runbook_revision.plain_text)
         )
       )
       or exists (
         select 1
           from ops.sourced_work_request_plan current_plan
           join public.doctrine_section planned_runbook_section
             on planned_runbook_section.id = current_plan.runbook_section_id
           join public.doctrine_document planned_runbook_document
             on planned_runbook_document.id = planned_runbook_section.document_id
           join public.doctrine_revision planned_runbook_revision
             on planned_runbook_revision.id = planned_runbook_section.current_revision_id
            and planned_runbook_revision.section_id = planned_runbook_section.id
          where current_plan.id = (
            select latest_plan.id
              from ops.sourced_work_request_plan latest_plan
             where latest_plan.work_request_id = w.id
             order by latest_plan.plan_version desc
             limit 1
          )
            and planned_runbook_document.slug = 'runbook'
            and planned_runbook_document.visibility = 'shared'
            and planned_runbook_section.status = 'active'
            and planned_runbook_revision.id = current_plan.runbook_revision_id
            and planned_runbook_revision.content_hash = current_plan.runbook_content_hash
            and ('doctrine:' || planned_runbook_document.slug || '#' || planned_runbook_section.section_key) = current_plan.runbook_ref
            and encode(public.digest(planned_runbook_revision.plain_text, 'sha256'), 'hex') = planned_runbook_revision.content_hash
            and planned_runbook_revision.body = jsonb_build_object('text', planned_runbook_revision.plain_text)
       )
     )
   order by w.captured_at asc, w.ref asc
   limit 20;
$$;

revoke all on function ops.current_sourced_work_requests(text) from public;
revoke execute on function ops.current_sourced_work_requests(text) from carr_jobs, carr_authority;
grant execute on function ops.current_sourced_work_requests(text) to carr_reader, carr_writer;

commit;
