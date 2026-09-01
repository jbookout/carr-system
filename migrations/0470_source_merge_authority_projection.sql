-- 0470_source_merge_authority_projection.sql
--
-- Merge is a consequence of an already accepted plan, never a second human
-- approval ceremony.  The exact file boundary therefore has to be inside the
-- plan hash that Joe accepts.  Ownership leases and assurance manifests remain
-- coordination/evidence only; this migration never promotes either into
-- authority.

create table ops.source_merge_plan_scope (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (btrim(organization_tenant_id)<>''),
  work_request_id uuid not null unique references ops.work_request(id) on delete restrict,
  accepted_plan_id uuid not null unique references ops.sourced_work_request_plan(id) on delete restrict,
  acceptance_receipt_id uuid not null unique references ops.sourced_work_request_plan_acceptance_receipt(id) on delete restrict,
  accepted_by_actor_id uuid not null references public.actor(id) on delete restrict,
  repository text not null check (repository='jbookout/carr-system'),
  base_branch text not null check (base_branch='main'),
  authorized_paths jsonb not null check (jsonb_typeof(authorized_paths)='array' and jsonb_array_length(authorized_paths)>0),
  scope_digest text not null unique check (scope_digest ~ '^sha256:[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp()
);

comment on table ops.source_merge_plan_scope is
  'Append-only exact file boundary copied from the accepted plan hash at the human plan-acceptance transaction. It authorizes only later source merge eligibility; lease claims and reviewer facts cannot add paths.';

create or replace function ops.source_merge_scope_valid(p_scope jsonb)
returns boolean language plpgsql immutable strict
set search_path=pg_catalog,ops,public as $$
begin
  if jsonb_typeof(p_scope) is distinct from 'object'
     or (select array_agg(k order by k) from jsonb_object_keys(p_scope) k)
        is distinct from array['authorized_paths','base_branch','repository','schema_version']::text[]
     or p_scope->>'schema_version' is distinct from 'source-merge-scope.v1'
     or p_scope->>'repository' is distinct from 'jbookout/carr-system'
     or p_scope->>'base_branch' is distinct from 'main'
     or jsonb_typeof(p_scope->'authorized_paths') is distinct from 'array'
     or jsonb_array_length(p_scope->'authorized_paths') not between 1 and 100 then
    return false;
  end if;
  if exists (
       select 1 from jsonb_array_elements(p_scope->'authorized_paths') item
        where jsonb_typeof(item) is distinct from 'string'
     ) then
    return false;
  end if;
  if exists (
       select 1 from jsonb_array_elements_text(p_scope->'authorized_paths') path
        where char_length(path) not between 1 and 500
           or path !~ '^[!-~]+$'
           or position(chr(92) in path)>0
           or path ~ '(^/|/$|//|[?*\[\]{}!]|(^|/)\.\.?(/|$))'
     ) or exists (
       select 1 from jsonb_array_elements_text(p_scope->'authorized_paths') path
        group by lower(path) having count(*)>1
     ) or (select jsonb_agg(to_jsonb(path) order by lower(path) collate "C",path collate "C")
             from jsonb_array_elements_text(p_scope->'authorized_paths') path)
          is distinct from p_scope->'authorized_paths' then
    return false;
  end if;
  return true;
end
$$;

create or replace function ops.capture_source_merge_plan_scope()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public as $$
declare p ops.sourced_work_request_plan%rowtype;
        w ops.work_request%rowtype;
        scope jsonb;
        normalized_paths jsonb;
        digest text;
begin
  select * into p from ops.sourced_work_request_plan where id=new.plan_id for share;
  select * into w from ops.work_request where id=new.work_request_id for share;
  scope:=p.caps->'source_merge';
  if scope is null then return new; end if;
  if p.work_request_id is distinct from new.work_request_id
     or p.plan_hash is distinct from new.plan_hash
     or new.accepted_by_actor_id is null
     or not coalesce(ops.source_merge_scope_valid(scope),false) then
    raise exception 'accepted source merge scope is not an exact valid plan-hash binding';
  end if;
  select jsonb_agg(to_jsonb(path) order by lower(path) collate "C",path collate "C")
    into normalized_paths from jsonb_array_elements_text(scope->'authorized_paths') path;
  digest:='sha256:'||encode(public.digest(jsonb_build_object(
    'accepted_plan_id',p.id,'plan_hash',p.plan_hash,'scope',scope)::text,'sha256'),'hex');
  insert into ops.source_merge_plan_scope(
    organization_tenant_id,work_request_id,accepted_plan_id,acceptance_receipt_id,
    accepted_by_actor_id,repository,base_branch,authorized_paths,scope_digest,created_at)
  values(w.organization_tenant_id,w.id,p.id,new.id,new.accepted_by_actor_id,
    scope->>'repository',scope->>'base_branch',normalized_paths,digest,new.accepted_at);
  return new;
end $$;

create trigger sourced_plan_acceptance_captures_source_merge_scope
after insert on ops.sourced_work_request_plan_acceptance_receipt
for each row execute function ops.capture_source_merge_plan_scope();

create or replace function ops.source_merge_rows_immutable()
returns trigger language plpgsql set search_path=pg_catalog,ops as $$
begin raise exception 'source merge plan scope is append-only'; end $$;

create trigger source_merge_plan_scope_immutable
before update or delete on ops.source_merge_plan_scope
for each row execute function ops.source_merge_rows_immutable();

-- Keep the existing plan contract compatible while allowing one optional,
-- typed source_merge object inside caps.  caps is already part of the canonical
-- plan preimage, so acceptance of plan_hash accepts this exact boundary too.
create or replace function ops.propose_sourced_work_request_plan(
  p_work_request text, p_base_version integer, p_scope_summary text,
  p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text,
  p_observability_ref text, p_caps jsonb, p_idempotency_key uuid
)
returns table (
  plan_id uuid, plan_ref text, plan_hash text, work_request_id uuid,
  ref text, state text, version integer, runbook_ref text,
  runbook_revision_id uuid, runbook_content_hash text, scope_summary text,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype; p ops.sourced_work_request_plan%rowtype;
  rb_section_id uuid; rb_revision_id uuid; rb_content_hash text;
  normalized_scope text; normalized_runbook text; normalized_recovery text; normalized_observability text;
  next_plan_version integer; canonical_preimage jsonb; canonical_hash text;
begin
  normalized_scope := btrim(p_scope_summary); normalized_runbook := btrim(p_runbook_ref);
  normalized_recovery := btrim(p_recovery_ref); normalized_observability := btrim(p_observability_ref);
  if p_idempotency_key is null or p_base_version is null or p_base_version < 1
     or coalesce(normalized_scope,'') = '' or char_length(normalized_scope) > 1000
     or coalesce(normalized_runbook,'') !~ '^doctrine:runbook#[a-z0-9][a-z0-9-]*$'
     or coalesce(normalized_recovery,'') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(normalized_recovery) > 300
     or coalesce(normalized_observability,'') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(normalized_observability) > 300 then
    raise exception 'invalid bounded sourced plan';
  end if;
  if jsonb_typeof(p_dependency_refs) is distinct from 'array' or jsonb_array_length(p_dependency_refs) > 12
     or exists (select 1 from jsonb_array_elements(p_dependency_refs) v where jsonb_typeof(v) <> 'string' or v #>> '{}' !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(v #>> '{}') > 300)
     or exists (select v #>> '{}' from jsonb_array_elements(p_dependency_refs) v group by v #>> '{}' having count(*) > 1) then
    raise exception 'invalid bounded sourced plan dependency references';
  end if;
  if jsonb_typeof(p_caps) is distinct from 'object'
     or ((select array_agg(k order by k) from jsonb_object_keys(p_caps) k)
           is distinct from array['max_duration_minutes','max_steps']::text[]
         and (select array_agg(k order by k) from jsonb_object_keys(p_caps) k)
           is distinct from array['max_duration_minutes','max_steps','source_merge']::text[])
     or coalesce(p_caps->>'max_steps','') !~ '^[0-9]+$' or coalesce(p_caps->>'max_duration_minutes','') !~ '^[0-9]+$'
     or (p_caps->>'max_steps')::integer not between 1 and 20 or (p_caps->>'max_duration_minutes')::integer not between 1 and 120
     or (p_caps ? 'source_merge' and not coalesce(ops.source_merge_scope_valid(p_caps->'source_merge'),false)) then
    raise exception 'invalid bounded sourced plan caps';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-plan-proposal:' || p_idempotency_key, 0));
  select x.* into p from ops.sourced_work_request_plan x where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into w from ops.work_request x where x.id=p.work_request_id for share;
    if not found or w.ref is distinct from p_work_request or p.work_request_version is distinct from p_base_version
       or p.scope_summary is distinct from normalized_scope or p.runbook_ref is distinct from normalized_runbook
       or p.dependency_refs is distinct from p_dependency_refs or p.recovery_ref is distinct from normalized_recovery
       or p.observability_ref is distinct from normalized_observability or p.caps is distinct from p_caps then
      raise exception 'idempotency key already names a different sourced plan proposal';
    end if;
    return query select p.id,p.plan_ref,p.plan_hash,w.id,w.ref,'triaged'::text,p.work_request_version,
      p.runbook_ref,p.runbook_revision_id,'sha256:' || p.runbook_content_hash,p.scope_summary,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found or w.state is distinct from 'triaged' or w.version is distinct from p_base_version
     or w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal' then
    raise exception 'exact current triaged sourced Work Request required';
  end if;
  if not (
    ((w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
       is not distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
      and not exists (select 1 from ops.work_shape_revision sr where sr.work_request_id=w.id))
    or
    (w.shape_disposition='required' and w.shape_fixed_surface_ref is null and w.shape_rationale is not null and btrim(w.shape_rationale) <> ''
      and exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r where r.work_request_id=w.id and r.result_version=w.version
                    and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                        is not distinct from (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at))
      and (select sr.work_request_version from ops.work_shape_revision sr where sr.work_request_id=w.id order by sr.version desc limit 1)=w.version)
    or
    (w.shape_disposition='not_required' and w.shape_fixed_surface_ref is not null and btrim(w.shape_fixed_surface_ref) <> ''
      and w.shape_rationale is not null and btrim(w.shape_rationale) <> ''
      and exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r where r.work_request_id=w.id and r.result_version=w.version
                    and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                        is not distinct from (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at))
      and not exists (select 1 from ops.work_shape_revision sr where sr.work_request_id=w.id))
  ) then
    raise exception 'exact unshaped or current receipt-backed shaped triaged sourced Work Request required';
  end if;

  perform 1 from public.doctrine_document source_document join public.doctrine_section source_section on source_section.document_id=source_document.id
    join public.doctrine_revision source_revision on source_revision.id=source_section.current_revision_id and source_revision.section_id=source_section.id
   where source_document.visibility='shared' and source_section.status='active' and source_section.id=w.doctrine_section_id
     and source_revision.id=w.doctrine_revision_id and source_revision.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(source_revision.plain_text,'sha256'),'hex')=source_revision.content_hash
     and source_revision.body=jsonb_build_object('text',source_revision.plain_text)
   for share of source_document,source_section,source_revision;
  if not found then raise exception 'sourced Work Request evidence is no longer exact, current, active, and shared'; end if;
  select s.id,r.id,r.content_hash into rb_section_id,rb_revision_id,rb_content_hash
    from public.doctrine_document d join public.doctrine_section s on s.document_id=d.id
    join public.doctrine_revision r on r.id=s.current_revision_id and r.section_id=s.id
   where d.slug='runbook' and d.visibility='shared' and s.status='active'
     and ('doctrine:' || d.slug || '#' || s.section_key)=normalized_runbook and r.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(r.plain_text,'sha256'),'hex')=r.content_hash and r.body=jsonb_build_object('text',r.plain_text)
   for share of d,s,r;
  if not found then raise exception 'runbook must be an exact current active shared doctrine revision'; end if;
  select coalesce(max(x.plan_version),0)+1 into next_plan_version from ops.sourced_work_request_plan x where x.work_request_id=w.id;
  canonical_preimage := ops.sourced_work_request_plan_preimage(w.id,normalized_scope,normalized_runbook,rb_section_id,rb_revision_id,rb_content_hash,p_dependency_refs,normalized_recovery,normalized_observability,p_caps);
  canonical_hash := ops.sourced_work_request_plan_digest(canonical_preimage);
  if exists (select 1 from ops.sourced_work_request_plan x where x.work_request_id=w.id and x.plan_hash=canonical_hash) then
    raise exception 'the exact sourced plan already exists under a different idempotency key';
  end if;
  insert into ops.sourced_work_request_plan (work_request_id,plan_version,idempotency_key,work_request_version,preimage,scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
  values (w.id,next_plan_version,p_idempotency_key,w.version,canonical_preimage,normalized_scope,normalized_runbook,rb_section_id,rb_revision_id,rb_content_hash,p_dependency_refs,normalized_recovery,normalized_observability,p_caps,canonical_hash,'PLAN-' || substr(canonical_hash,8,12) || '-v' || next_plan_version)
  returning * into p;
  return query select p.id,p.plan_ref,p.plan_hash,w.id,w.ref,w.state,w.version,p.runbook_ref,p.runbook_revision_id,'sha256:' || p.runbook_content_hash,p.scope_summary,false;
end;
$$;

create or replace function ops.source_merge_authority_projection(
  p_decision_id uuid,p_work_request text,p_head_sha text,p_pr_number integer
) returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,ops,public as $$
declare tenant constant text:='carr-internal';
        work_ref text:=nullif(btrim(p_work_request),'');
        candidate_count integer;
        w ops.work_request%rowtype;
        p ops.sourced_work_request_plan%rowtype;
        ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
        scope ops.source_merge_plan_scope%rowtype;
        sp ops.engineering_slice_plan%rowtype;
        decision record;
        acceptor record;
        facts jsonb;
        evaluated_at timestamptz:=statement_timestamp();
begin
  if p_decision_id is null
     or (work_ref is not null and work_ref !~ '^WR-[0-9]{1,12}$')
     or coalesce(p_head_sha,'') !~ '^[0-9a-f]{40}$'
     or p_pr_number is null or p_pr_number<1 then
    return jsonb_build_object('ok',false,'error','source_merge_locator_invalid');
  end if;

  if work_ref is null then
    select min(candidate.ref),count(distinct candidate.id)
      into work_ref,candidate_count
      from ops.work_request candidate
      join ops.source_merge_plan_scope candidate_scope
        on candidate_scope.work_request_id=candidate.id
       and candidate_scope.organization_tenant_id=tenant
      join ops.engineering_slice_receipt candidate_receipt
        on candidate_receipt.work_request_id=candidate.id
       and candidate_receipt.outcome='claimed_complete'
       and candidate_receipt.receipt#>>'{source_evidence,source_sha}'=p_head_sha
      join ops.engineering_execution_envelope candidate_envelope
        on candidate_envelope.id=candidate_receipt.envelope_id
      left join ops.engineering_execution_envelope candidate_successor
        on candidate_successor.supersedes_envelope_id=candidate_envelope.id
     where candidate.organization_tenant_id=tenant
       and candidate.state='ready' and candidate.blocker_code is null
       and candidate_successor.id is null;
    if candidate_count is distinct from 1 then
      return jsonb_build_object('ok',false,'error','source_merge_candidate_not_unique');
    end if;
  end if;

  select e.id::text event_id,e.subject_id::text decision_id,e.new_value->>'title' title,
         e.sponsoring_human_slug
    into decision
    from public.event e
   where e.subject_type='decision' and e.verb='log-decision'
     and e.subject_id=p_decision_id
   order by e.recorded_at desc,e.id desc limit 1;
  if decision.event_id is null
     or decision.title is distinct from 'Routine authorized green PRs merge without asking Joe for ceremonial approval'
     or decision.sponsoring_human_slug is distinct from 'joe' then
    return jsonb_build_object('ok',false,'error','source_merge_decision_not_current');
  end if;

  select x.* into w from ops.work_request x
   where x.ref=work_ref and x.organization_tenant_id=tenant;
  if not found or w.state is distinct from 'ready' or w.blocker_code is not null then
    return jsonb_build_object('ok',false,'error','source_merge_work_not_current');
  end if;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.work_request_id=w.id;
  select x.* into p from ops.sourced_work_request_plan x where x.id=ar.plan_id;
  select x.* into scope from ops.source_merge_plan_scope x
   where x.work_request_id=w.id and x.accepted_plan_id=p.id
     and x.acceptance_receipt_id=ar.id and x.organization_tenant_id=tenant;
  select a.id,a.slug into acceptor from public.actor a
   where a.id=ar.accepted_by_actor_id and a.slug='joe' and a.kind='human' and a.active;
  if ar.id is null or p.id is null or scope.id is null or acceptor.id is null
     or ar.result_version is distinct from w.version
     or ar.plan_hash is distinct from p.plan_hash
     or scope.accepted_by_actor_id is distinct from ar.accepted_by_actor_id
     or not coalesce(ops.source_merge_scope_valid(jsonb_build_object(
          'schema_version','source-merge-scope.v1','repository',scope.repository,
          'base_branch',scope.base_branch,'authorized_paths',scope.authorized_paths)),false) then
    return jsonb_build_object('ok',false,'error','source_merge_plan_scope_not_authorized');
  end if;

  select x.* into sp from ops.engineering_slice_plan x
   where x.accepted_plan_id=p.id and x.work_request_id=w.id
     and x.accepted_plan_hash=p.plan_hash and x.work_request_version=w.version;
  if not found or sp.plan#>>'{work_request,state_version}' is distinct from w.version::text
     or sp.plan#>>'{accepted_plan_revision,digest}' is distinct from p.plan_hash
     or jsonb_typeof(sp.plan->'slices') is distinct from 'array'
     or jsonb_array_length(sp.plan->'slices')=0
     or exists(select 1 from jsonb_array_elements(sp.plan->'slices') slice
                where slice->'manual_qa_required' is distinct from 'false'::jsonb) then
    return jsonb_build_object('ok',false,'error','source_merge_slice_plan_not_current_or_requires_manual_qa');
  end if;

  facts:=ops.engineering_passport_facts(work_ref);
  if facts is null or jsonb_typeof(facts->'source') is distinct from 'object' then
    return jsonb_build_object('ok',false,'error','source_merge_passport_unavailable');
  end if;

  return jsonb_build_object(
    'ok',true,
    'passport_facts',facts,
    'authority',jsonb_build_object(
      'schema_version','source-merge-authority.v1',
      'derived_by','source-merge-authority-projection',
      'decision',jsonb_build_object(
        'decision_ref','decision:'||decision.decision_id,
        'event_ref','event:'||decision.event_id,
        'sponsoring_human_slug',decision.sponsoring_human_slug,
        'title',decision.title),
      'exact_head_sha',p_head_sha,
      'pr_number',p_pr_number,
      'source_merge_only',true,
      'allowed_actions',jsonb_build_array('repository:merge-pr'),
      'scope_ref','source-merge-scope:'||scope.id::text,
      'scope_digest',scope.scope_digest,
      'authorized_path_claims',(select jsonb_agg(jsonb_build_object(
          'path',path,'mode','file','operation','write') order by lower(path) collate "C",path collate "C")
        from jsonb_array_elements_text(scope.authorized_paths) path),
      'currentness_evaluated_at',evaluated_at));
exception when others then
  return jsonb_build_object('ok',false,'error','source_merge_projection_refused','sqlstate',sqlstate);
end $$;

revoke all on ops.source_merge_plan_scope from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.source_merge_scope_valid(jsonb),ops.capture_source_merge_plan_scope(),
  ops.source_merge_rows_immutable(),ops.source_merge_authority_projection(uuid,text,text,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.source_merge_authority_projection(uuid,text,text,integer) to carr_reader;

do $$
begin
  if has_table_privilege('carr_reader','ops.source_merge_plan_scope','select')
     or not has_function_privilege('carr_reader','ops.source_merge_authority_projection(uuid,text,text,integer)','execute')
     or has_function_privilege('carr_reader','ops.source_merge_scope_valid(jsonb)','execute') then
    raise exception '0470 FAILED: source merge projection privilege boundary is not narrow';
  end if;
end $$;
