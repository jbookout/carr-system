-- Program 6: freeze one bounded, pre-authored runbook plan and let an
-- authority-authenticated human accept that exact plan into ready.
--
-- Proposal is append-only and changes no Work Request state. Acceptance is the
-- sole new transition, triaged -> ready. Neither function assigns, dispatches,
-- executes, or grants an executor any capability.

begin;

create table ops.sourced_work_request_plan (
  id                    uuid primary key default gen_random_uuid(),
  work_request_id       uuid not null references ops.work_request(id),
  plan_version          integer not null check (plan_version > 0),
  idempotency_key       uuid not null unique,
  work_request_version  integer not null check (work_request_version > 0),
  preimage              jsonb not null check (jsonb_typeof(preimage) = 'object'),
  scope_summary         text not null check (btrim(scope_summary) <> '' and char_length(scope_summary) <= 1000),
  runbook_ref           text not null check (runbook_ref ~ '^doctrine:runbook#[a-z0-9][a-z0-9-]*$'),
  runbook_section_id    uuid not null references public.doctrine_section(id),
  runbook_revision_id   uuid not null references public.doctrine_revision(id),
  runbook_content_hash  text not null check (runbook_content_hash ~ '^[0-9a-f]{64}$'),
  dependency_refs       jsonb not null check (jsonb_typeof(dependency_refs) = 'array'),
  recovery_ref          text not null check (recovery_ref ~ '^safe:[a-z0-9][a-z0-9:_./-]*$' and char_length(recovery_ref) <= 300),
  observability_ref     text not null check (observability_ref ~ '^safe:[a-z0-9][a-z0-9:_./-]*$' and char_length(observability_ref) <= 300),
  caps                  jsonb not null check (jsonb_typeof(caps) = 'object'),
  plan_hash             text not null check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  plan_ref              text not null unique check (plan_ref ~ '^PLAN-[0-9a-f]{12}-v[1-9][0-9]*$'),
  created_at            timestamptz not null default now(),
  unique (work_request_id, plan_version),
  unique (work_request_id, plan_hash)
);

create table ops.sourced_work_request_plan_acceptance_receipt (
  id                    uuid primary key default gen_random_uuid(),
  work_request_id       uuid not null unique references ops.work_request(id),
  plan_id               uuid not null unique references ops.sourced_work_request_plan(id),
  idempotency_key       uuid not null unique,
  base_version          integer not null check (base_version > 0),
  plan_hash             text not null check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  accepted_by_actor_id  uuid not null references public.actor(id),
  accepted_at           timestamptz not null default now(),
  result_version        integer not null check (result_version > 0),
  shape_fixed_surface_ref text not null,
  shape_rationale       text not null
);

comment on table ops.sourced_work_request_plan is
  'Append-only Program 6 plan preimage. It binds a triaged sourced Work Request to one current shared pre-authored runbook and bounded metadata; it is not executable.';
comment on table ops.sourced_work_request_plan_acceptance_receipt is
  'Private human-authority receipt for the sole Program 6 triaged-to-ready transition. It grants no dispatch or execution authority.';

create or replace function ops.sourced_work_plan_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'Program 6 sourced plan and acceptance receipts are append-only';
end;
$$;

create trigger sourced_work_request_plan_immutable
before update or delete on ops.sourced_work_request_plan
for each row execute function ops.sourced_work_plan_rows_immutable();

create trigger sourced_work_request_plan_acceptance_immutable
before update or delete on ops.sourced_work_request_plan_acceptance_receipt
for each row execute function ops.sourced_work_plan_rows_immutable();

-- A canonical JSONB preimage gives proposal and acceptance one shared digest
-- algorithm. Acceptance calls it from current durable rows rather than trusting
-- the copy stored on the proposal.
create or replace function ops.sourced_work_request_plan_preimage(
  p_work_request_id uuid,
  p_scope_summary text,
  p_runbook_ref text,
  p_runbook_section_id uuid,
  p_runbook_revision_id uuid,
  p_runbook_content_hash text,
  p_dependency_refs jsonb,
  p_recovery_ref text,
  p_observability_ref text,
  p_caps jsonb
)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'contract', 'carr-sourced-ready-plan/v1',
    'work_request', jsonb_build_object(
      'id', w.id, 'ref', w.ref, 'state', w.state, 'version', w.version,
      'title', w.title, 'desired_outcome', w.desired_outcome,
      'acceptance_criteria', w.acceptance_criteria, 'origin_ref', w.origin_ref,
      'doctrine_section_id', w.doctrine_section_id,
      'doctrine_revision_id', w.doctrine_revision_id,
      'doctrine_content_hash', (
        select 'sha256:' || sr.content_hash from public.doctrine_revision sr
         where sr.id = w.doctrine_revision_id and sr.section_id = w.doctrine_section_id
      ),
      'triage_classification', w.triage_classification,
      'triaged_by_actor_id', w.triaged_by_actor_id, 'triaged_at', w.triaged_at,
      'shape_disposition', w.shape_disposition,
      'shape_fixed_surface_ref', w.shape_fixed_surface_ref,
      'shape_rationale', w.shape_rationale,
      'shape_decided_by_actor_id', w.shape_decided_by_actor_id,
      'shape_decided_at', w.shape_decided_at
    ),
    'runbook', jsonb_build_object(
      'ref', p_runbook_ref, 'section_id', p_runbook_section_id,
      'revision_id', p_runbook_revision_id,
      'content_hash', 'sha256:' || p_runbook_content_hash
    ),
    'plan', jsonb_build_object(
      'scope_summary', p_scope_summary, 'dependency_refs', p_dependency_refs,
      'recovery_ref', p_recovery_ref,
      'observability_ref', p_observability_ref, 'caps', p_caps
    )
  )
  from ops.work_request w where w.id = p_work_request_id;
$$;

create or replace function ops.sourced_work_request_plan_digest(p_preimage jsonb)
returns text language sql immutable security definer
set search_path = pg_catalog, ops
as $$
  select 'sha256:' || encode(public.digest(p_preimage::text, 'sha256'), 'hex');
$$;

revoke all on function ops.sourced_work_request_plan_preimage(uuid,text,text,uuid,uuid,text,jsonb,text,text,jsonb) from public;
revoke all on function ops.sourced_work_request_plan_digest(jsonb) from public;

-- Extend, rather than replace, the 0175 receipt boundary. The first branch is
-- the existing human triage path; the second is the exact accepted-plan path.
create or replace function ops.sourced_work_request_is_immutable()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops
as $$
begin
  if old.capture_idempotency_key is null then
    return new;
  end if;

  if old.state = 'captured'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
     and exists (
       select 1 from ops.work_request_triage_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version
          and r.classification = new.triage_classification
          and r.triaged_by_actor_id = new.triaged_by_actor_id
          and r.triaged_at = new.triaged_at
     ) then
    return new;
  end if;

  if old.state = 'triaged'
     and new.state = 'ready'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','version','updated_at','shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at'])
           is not distinct from
         (to_jsonb(old) - array['state','version','updated_at','shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at'])
     and exists (
       select 1
         from ops.sourced_work_request_plan_acceptance_receipt ar
         join ops.sourced_work_request_plan p on p.id = ar.plan_id
        where ar.work_request_id = old.id and ar.base_version = old.version
          and ar.result_version = new.version and ar.plan_hash = p.plan_hash
          and new.shape_disposition = 'not_required'
          and new.shape_fixed_surface_ref = ar.shape_fixed_surface_ref
          and new.shape_fixed_surface_ref = ('sourced-plan:' || p.plan_ref || '#' || p.plan_hash)
          and new.shape_rationale = ar.shape_rationale
          and new.shape_rationale = ('Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref || ' at sha256:' || p.runbook_content_hash)
          and new.shape_decided_by_actor_id = ar.accepted_by_actor_id
          and new.shape_decided_at = ar.accepted_at
     ) then
    return new;
  end if;

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged or triaged-to-ready transitions';
end;
$$;

alter table ops.work_request drop constraint if exists work_request_sourced_capture_shape;
alter table ops.work_request add constraint work_request_sourced_capture_shape check (
  (capture_idempotency_key is null and organization_tenant_id is null
    and doctrine_section_id is null and doctrine_revision_id is null
    and sourced_capture_sequence is null and triage_classification is null
    and triaged_by_actor_id is null and triaged_at is null)
  or (capture_idempotency_key is not null and organization_tenant_id = 'carr-internal'
    and doctrine_section_id is not null and doctrine_revision_id is not null
    and sourced_capture_sequence is not null and program_key is null and program_ordinal is null
    and origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'
    and (
      (state = 'captured' and triage_classification is null
       and triaged_by_actor_id is null and triaged_at is null)
      or (state = 'triaged'
       and triage_classification in ('operational','needs_judgment','safety_review')
       and triaged_by_actor_id is not null and triaged_at is not null)
      or (state = 'ready'
       and triage_classification in ('operational','needs_judgment','safety_review')
       and triaged_by_actor_id is not null and triaged_at is not null
       and shape_disposition = 'not_required'
       and shape_fixed_surface_ref ~ '^sourced-plan:PLAN-[0-9a-f]{12}-v[1-9][0-9]*#sha256:[0-9a-f]{64}$'
       and shape_rationale ~ '^Accepted immutable plan PLAN-[0-9a-f]{12}-v[1-9][0-9]* for doctrine:runbook#[a-z0-9][a-z0-9-]* at sha256:[0-9a-f]{64}$'
       and shape_decided_by_actor_id is not null and shape_decided_at is not null)
    ))
) not valid;

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
  w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  rb_section_id uuid; rb_revision_id uuid; rb_content_hash text;
  normalized_scope text; normalized_runbook text;
  normalized_recovery text; normalized_observability text;
  next_plan_version integer; canonical_preimage jsonb; canonical_hash text;
begin
  normalized_scope := btrim(p_scope_summary);
  normalized_runbook := btrim(p_runbook_ref);
  normalized_recovery := btrim(p_recovery_ref);
  normalized_observability := btrim(p_observability_ref);
  if p_idempotency_key is null or p_base_version is null or p_base_version < 1
     or coalesce(normalized_scope, '') = '' or char_length(normalized_scope) > 1000
     or coalesce(normalized_runbook, '') !~ '^doctrine:runbook#[a-z0-9][a-z0-9-]*$'
     or coalesce(normalized_recovery, '') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
     or char_length(normalized_recovery) > 300
     or coalesce(normalized_observability, '') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
     or char_length(normalized_observability) > 300 then
    raise exception 'invalid bounded sourced plan';
  end if;
  if jsonb_typeof(p_dependency_refs) is distinct from 'array'
     or jsonb_array_length(p_dependency_refs) > 12
     or exists (select 1 from jsonb_array_elements(p_dependency_refs) v
                 where jsonb_typeof(v) <> 'string'
                    or v #>> '{}' !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
                    or char_length(v #>> '{}') > 300)
     or exists (select v #>> '{}' from jsonb_array_elements(p_dependency_refs) v
                 group by v #>> '{}' having count(*) > 1) then
    raise exception 'invalid bounded sourced plan dependency references';
  end if;
  if jsonb_typeof(p_caps) is distinct from 'object'
     or (select array_agg(k order by k) from jsonb_object_keys(p_caps) k)
        is distinct from array['max_duration_minutes','max_steps']::text[]
     or coalesce(p_caps->>'max_steps','') !~ '^[0-9]+$'
     or coalesce(p_caps->>'max_duration_minutes','') !~ '^[0-9]+$' then
    raise exception 'invalid bounded sourced plan caps';
  end if;
  if (p_caps->>'max_steps')::integer not between 1 and 20
     or (p_caps->>'max_duration_minutes')::integer not between 1 and 120 then
    raise exception 'invalid bounded sourced plan caps';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-plan-proposal:' || p_idempotency_key, 0));
  select x.* into p from ops.sourced_work_request_plan x
   where x.idempotency_key = p_idempotency_key for share;
  if found then
    select x.* into w from ops.work_request x where x.id = p.work_request_id for share;
    if not found or w.ref is distinct from p_work_request
       or p.work_request_version is distinct from p_base_version
       or p.scope_summary is distinct from normalized_scope
       or p.runbook_ref is distinct from normalized_runbook
       or p.dependency_refs is distinct from p_dependency_refs
       or p.recovery_ref is distinct from normalized_recovery
       or p.observability_ref is distinct from normalized_observability
       or p.caps is distinct from p_caps then
      raise exception 'idempotency key already names a different sourced plan proposal';
    end if;
    return query select p.id, p.plan_ref, p.plan_hash, w.id, w.ref, 'triaged'::text,
      p.work_request_version, p.runbook_ref, p.runbook_revision_id,
      'sha256:' || p.runbook_content_hash, p.scope_summary, true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found or w.state is distinct from 'triaged'
     or w.version is distinct from p_base_version
     or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,
         w.shape_decided_by_actor_id,w.shape_decided_at)
        is distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
     or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
    raise exception 'exact unshaped triaged sourced Work Request required';
  end if;

  perform 1
    from public.doctrine_document source_document
    join public.doctrine_section source_section on source_section.document_id = source_document.id
    join public.doctrine_revision source_revision
      on source_revision.id = source_section.current_revision_id
     and source_revision.section_id = source_section.id
   where source_document.visibility = 'shared' and source_section.status = 'active'
     and source_section.id = w.doctrine_section_id
     and source_revision.id = w.doctrine_revision_id
     and source_revision.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(source_revision.plain_text,'sha256'),'hex') = source_revision.content_hash
     and source_revision.body = jsonb_build_object('text',source_revision.plain_text)
   for share of source_document, source_section, source_revision;
  if not found then
    raise exception 'sourced Work Request evidence is no longer exact, current, active, and shared';
  end if;

  select s.id, r.id, r.content_hash
    into rb_section_id, rb_revision_id, rb_content_hash
    from public.doctrine_document d
    join public.doctrine_section s on s.document_id = d.id
    join public.doctrine_revision r on r.id = s.current_revision_id and r.section_id = s.id
   where d.slug = 'runbook' and d.visibility = 'shared' and s.status = 'active'
     and ('doctrine:' || d.slug || '#' || s.section_key) = normalized_runbook
     and r.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(r.plain_text,'sha256'),'hex') = r.content_hash
     and r.body = jsonb_build_object('text',r.plain_text)
   for share of d, s, r;
  if not found then
    raise exception 'runbook must be an exact current active shared doctrine revision';
  end if;

  select coalesce(max(x.plan_version),0) + 1 into next_plan_version
    from ops.sourced_work_request_plan x where x.work_request_id = w.id;
  canonical_preimage := ops.sourced_work_request_plan_preimage(
    w.id, normalized_scope, normalized_runbook, rb_section_id, rb_revision_id,
    rb_content_hash, p_dependency_refs, normalized_recovery,
    normalized_observability, p_caps);
  canonical_hash := ops.sourced_work_request_plan_digest(canonical_preimage);
  if exists (select 1 from ops.sourced_work_request_plan x
              where x.work_request_id = w.id and x.plan_hash = canonical_hash) then
    raise exception 'the exact sourced plan already exists under a different idempotency key';
  end if;

  insert into ops.sourced_work_request_plan
    (work_request_id,plan_version,idempotency_key,work_request_version,preimage,
     scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,
     runbook_content_hash,dependency_refs,recovery_ref,observability_ref,caps,
     plan_hash,plan_ref)
  values
    (w.id,next_plan_version,p_idempotency_key,w.version,canonical_preimage,
     normalized_scope,normalized_runbook,rb_section_id,rb_revision_id,
     rb_content_hash,p_dependency_refs,normalized_recovery,normalized_observability,
     p_caps,canonical_hash,
     'PLAN-' || substr(canonical_hash,8,12) || '-v' || next_plan_version)
  returning * into p;
  return query select p.id, p.plan_ref, p.plan_hash, w.id, w.ref, w.state,
    w.version, p.runbook_ref, p.runbook_revision_id,
    'sha256:' || p.runbook_content_hash, p.scope_summary, false;
end;
$$;

create or replace function ops.accept_sourced_work_request_plan(
  p_work_request text, p_base_version integer, p_plan_hash text,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid, ref text, state text, version integer,
  plan_id uuid, plan_ref text, plan_hash text,
  accepted_by_actor_slug text, accepted_at timestamptz,
  shape_disposition text, shape_fixed_surface_ref text, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  actor_slug text; a public.actor%rowtype; w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  canonical_preimage jsonb; canonical_hash text; fixed_surface text; rationale text;
begin
  if p_idempotency_key is null or p_base_version is null or p_base_version < 1
     or coalesce(p_plan_hash,'') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'exact sourced plan acceptance inputs required';
  end if;
  actor_slug := ops.authority_actor_slug();
  select x.* into a from public.actor x
   where x.slug = actor_slug and x.active and x.kind = 'human' for share;
  if not found then
    raise exception 'authority session user is not an active human actor';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-plan-acceptance:' || p_idempotency_key, 0));
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.idempotency_key = p_idempotency_key for share;
  if found then
    select x.* into p from ops.sourced_work_request_plan x where x.id = ar.plan_id for share;
    select x.* into w from ops.work_request x where x.id = ar.work_request_id for share;
    if not found or w.ref is distinct from p_work_request
       or ar.base_version is distinct from p_base_version
       or ar.plan_hash is distinct from p_plan_hash
       or ar.accepted_by_actor_id is distinct from a.id
       or p.id is distinct from ar.plan_id or p.plan_hash is distinct from ar.plan_hash
       or w.state is distinct from 'ready' or w.version is distinct from ar.result_version
       or w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref then
      raise exception 'idempotency key already names a different sourced plan acceptance';
    end if;
    return query select w.id,w.ref,w.state,w.version,p.id,p.plan_ref,p.plan_hash,
      a.slug,ar.accepted_at,w.shape_disposition,w.shape_fixed_surface_ref,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  select x.* into p from ops.sourced_work_request_plan x
   where x.work_request_id = w.id and x.plan_hash = p_plan_hash for share;
  if not found or w.state is distinct from 'triaged'
     or w.version is distinct from p_base_version
     or p.work_request_version is distinct from p_base_version
     or w.capture_idempotency_key is null
     or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,
         w.shape_decided_by_actor_id,w.shape_decided_at)
        is distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
     or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
    raise exception 'exact unshaped triaged sourced plan/version required';
  end if;
  perform 1 from public.doctrine_document d
    join public.doctrine_section s on s.document_id = d.id
    join public.doctrine_revision r on r.id = s.current_revision_id and r.section_id = s.id
    where d.slug = 'runbook' and d.visibility = 'shared' and s.status = 'active'
      and s.id = p.runbook_section_id and r.id = p.runbook_revision_id
      and r.content_hash = p.runbook_content_hash
      and ('doctrine:' || d.slug || '#' || s.section_key) = p.runbook_ref
      and encode(public.digest(r.plain_text,'sha256'),'hex') = r.content_hash
      and r.body = jsonb_build_object('text',r.plain_text)
    for share of d, s, r;
  if not found then
    raise exception 'accepted sourced plan runbook is no longer current';
  end if;
  perform 1
    from public.doctrine_document source_document
    join public.doctrine_section source_section on source_section.document_id = source_document.id
    join public.doctrine_revision source_revision
      on source_revision.id = source_section.current_revision_id
     and source_revision.section_id = source_section.id
   where source_document.visibility = 'shared' and source_section.status = 'active'
     and source_section.id = w.doctrine_section_id
     and source_revision.id = w.doctrine_revision_id
     and source_revision.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(source_revision.plain_text,'sha256'),'hex') = source_revision.content_hash
     and source_revision.body = jsonb_build_object('text',source_revision.plain_text)
   for share of source_document, source_section, source_revision;
  if not found then
    raise exception 'accepted sourced plan source evidence is no longer current';
  end if;

  canonical_preimage := ops.sourced_work_request_plan_preimage(
    w.id,p.scope_summary,p.runbook_ref,p.runbook_section_id,p.runbook_revision_id,
    p.runbook_content_hash,p.dependency_refs,p.recovery_ref,p.observability_ref,p.caps);
  canonical_hash := ops.sourced_work_request_plan_digest(canonical_preimage);
  if canonical_preimage is distinct from p.preimage
     or canonical_hash is distinct from p.plan_hash
     or canonical_hash is distinct from p_plan_hash then
    raise exception 'sourced plan preimage is stale or does not match its exact hash';
  end if;

  fixed_surface := 'sourced-plan:' || p.plan_ref || '#' || p.plan_hash;
  rationale := 'Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref ||
               ' at sha256:' || p.runbook_content_hash;
  insert into ops.sourced_work_request_plan_acceptance_receipt
    (work_request_id,plan_id,idempotency_key,base_version,plan_hash,
     accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
  values
    (w.id,p.id,p_idempotency_key,p_base_version,p_plan_hash,a.id,w.version + 1,
     fixed_surface,rationale)
  returning * into ar;

  update ops.work_request x
     set state = 'ready', version = ar.result_version, updated_at = now(),
         shape_disposition = 'not_required',
         shape_fixed_surface_ref = ar.shape_fixed_surface_ref,
         shape_rationale = ar.shape_rationale,
         shape_decided_by_actor_id = a.id,
         shape_decided_at = ar.accepted_at
   where x.id = w.id;
  select x.* into w from ops.work_request x where x.id = w.id;
  return query select w.id,w.ref,w.state,w.version,p.id,p.plan_ref,p.plan_hash,
    a.slug,ar.accepted_at,w.shape_disposition,w.shape_fixed_surface_ref,false;
end;
$$;

-- Safe read projection. Private proposal/receipt tables remain unreadable by
-- carr_reader/carr_writer; this definer returns only bounded Program 6 metadata.
drop function ops.work_request_card(text,text);
create or replace function ops.work_request_card(
  p_work_request text, p_organization_tenant_id text
)
returns table (
  ref text, title text, state text, version integer, origin_ref text,
  desired_outcome text, acceptance_criteria jsonb, doctrine_section_id uuid,
  doctrine_revision_id uuid, doctrine_source_label text, source_current boolean,
  triage_classification text, triaged_by_actor_slug text, triaged_at timestamptz,
  plan_ref text, plan_hash text, scope_summary text, runbook_ref text, runbook_revision_id uuid,
  runbook_content_hash text, plan_caps jsonb, dependency_refs jsonb,
  recovery_ref text, observability_ref text, accepted_by_actor_slug text,
  accepted_at timestamptz, shape_disposition text, shape_fixed_surface_ref text
)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select w.ref,w.title,w.state,w.version,w.origin_ref,w.desired_outcome,
         w.acceptance_criteria,w.doctrine_section_id,w.doctrine_revision_id,
         coalesce(s.title,s.section_key),
         (s.status='active' and s.current_revision_id=w.doctrine_revision_id),
         w.triage_classification,ta.slug,w.triaged_at,
         p.plan_ref,p.plan_hash,p.scope_summary,p.runbook_ref,p.runbook_revision_id,
         case when p.runbook_content_hash is null then null else 'sha256:' || p.runbook_content_hash end,
         p.caps,p.dependency_refs,p.recovery_ref,p.observability_ref,aa.slug,ar.accepted_at,
         w.shape_disposition,w.shape_fixed_surface_ref
    from ops.work_request w
    join public.doctrine_section s on s.id=w.doctrine_section_id
    join public.doctrine_document d on d.id=s.document_id
    left join public.actor ta on ta.id=w.triaged_by_actor_id
    left join lateral (
      select x.* from ops.sourced_work_request_plan x
      left join ops.sourced_work_request_plan_acceptance_receipt accepted
        on accepted.plan_id=x.id
       where x.work_request_id=w.id
       order by (accepted.id is not null) desc,x.plan_version desc limit 1
    ) p on true
    left join ops.sourced_work_request_plan_acceptance_receipt ar on ar.plan_id=p.id
    left join public.actor aa on aa.id=ar.accepted_by_actor_id
   where p_organization_tenant_id='carr-internal'
     and w.organization_tenant_id='carr-internal' and w.ref=p_work_request
     and w.state in ('captured','triaged','ready') and d.visibility='shared';
$$;

revoke all on table ops.sourced_work_request_plan,
  ops.sourced_work_request_plan_acceptance_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.propose_sourced_work_request_plan(text,integer,text,text,jsonb,text,text,jsonb,uuid)
  from public,carr_reader,carr_jobs,carr_authority;
revoke all on function ops.accept_sourced_work_request_plan(text,integer,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.propose_sourced_work_request_plan(text,integer,text,text,jsonb,text,text,jsonb,uuid)
  to carr_writer;
grant execute on function ops.accept_sourced_work_request_plan(text,integer,text,uuid)
  to carr_authority;
revoke all on function ops.work_request_card(text,text) from public;
grant execute on function ops.work_request_card(text,text) to carr_reader,carr_writer;

commit;
