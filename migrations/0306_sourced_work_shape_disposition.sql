-- Program 6: a sourced triaged Work Request may record exactly one
-- receipt-backed implementation-shape disposition before a ready-plan is
-- accepted.  This is deliberately not a lifecycle escape hatch: it cannot
-- change state, source provenance, tenant, plan, or any non-shape field.

begin;

create table if not exists ops.sourced_work_request_shape_disposition_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null unique references ops.work_request(id),
  idempotency_key uuid not null unique,
  base_version integer not null check (base_version > 0),
  result_version integer not null check (result_version > 0),
  disposition text not null check (disposition in ('required','not_required')),
  fixed_surface_ref text,
  rationale text not null check (btrim(rationale) <> ''),
  decided_by_actor_id uuid not null references public.actor(id),
  decided_at timestamptz not null default now(),
  check (
    (disposition = 'required' and fixed_surface_ref is null)
    or
    (disposition = 'not_required' and fixed_surface_ref is not null and btrim(fixed_surface_ref) <> '')
  )
);

comment on table ops.sourced_work_request_shape_disposition_receipt is
  'Append-only exact receipt for the sole sourced triaged shape-disposition mutation. '
  'It is neither a plan acceptance nor a lifecycle transition.';

create table if not exists ops.sourced_work_request_plan_shape_binding_receipt (
  id uuid primary key default gen_random_uuid(),
  plan_acceptance_receipt_id uuid not null unique references ops.sourced_work_request_plan_acceptance_receipt(id),
  work_request_id uuid not null references ops.work_request(id),
  disposition text not null check (disposition in ('required','not_required')),
  fixed_surface_ref text,
  rationale text not null check (btrim(rationale) <> ''),
  decided_by_actor_id uuid not null references public.actor(id),
  decided_at timestamptz not null,
  created_at timestamptz not null default now(),
  check (
    (disposition = 'required' and fixed_surface_ref is null)
    or
    (disposition = 'not_required' and fixed_surface_ref is not null and btrim(fixed_surface_ref) <> '')
  )
);

comment on table ops.sourced_work_request_plan_shape_binding_receipt is
  'Append-only binding that tells a sourced ready-plan receipt to preserve an already receipt-backed shape disposition. '
  'Absent only for the legacy unshaped path, whose plan receipt itself supplies not_required.';

create or replace function ops.sourced_work_shape_receipts_are_immutable()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'sourced Work Request shape receipts are append-only';
end;
$$;

create trigger sourced_work_request_shape_disposition_immutable
before update or delete on ops.sourced_work_request_shape_disposition_receipt
for each row execute function ops.sourced_work_shape_receipts_are_immutable();

create trigger sourced_work_request_plan_shape_binding_immutable
before update or delete on ops.sourced_work_request_plan_shape_binding_receipt
for each row execute function ops.sourced_work_shape_receipts_are_immutable();

create or replace function ops.set_sourced_work_request_shape_disposition(
  p_work_request text,
  p_base_version integer,
  p_disposition text,
  p_fixed_surface_ref text,
  p_rationale text,
  p_decided_by_actor_id uuid,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid,
  ref text,
  state text,
  version integer,
  shape_disposition text,
  shape_fixed_surface_ref text,
  shape_rationale text,
  shape_decided_by_actor_id uuid,
  shape_decided_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  receipt ops.sourced_work_request_shape_disposition_receipt%rowtype;
  actor public.actor%rowtype;
  normalized_fixed_surface text := nullif(btrim(coalesce(p_fixed_surface_ref,'')), '');
  normalized_rationale text := nullif(btrim(coalesce(p_rationale,'')), '');
begin
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_disposition not in ('required','not_required')
     or normalized_rationale is null
     or p_decided_by_actor_id is null
     or p_idempotency_key is null
     or (p_disposition = 'required' and normalized_fixed_surface is not null)
     or (p_disposition = 'not_required' and normalized_fixed_surface is null) then
    raise exception 'sourced shape disposition requires exact Work Request/base version, closed disposition, exact fixed surface rule, rationale, active actor, and UUID idempotency key';
  end if;

  select a.* into actor from public.actor a
   where a.id = p_decided_by_actor_id and a.active
   for share;
  if not found then
    raise exception 'sourced shape disposition actor is not active';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-sourced-shape-disposition:' || p_idempotency_key, 0));
  select r.* into receipt
    from ops.sourced_work_request_shape_disposition_receipt r
   where r.idempotency_key = p_idempotency_key
   for share;
  if found then
    select x.* into w from ops.work_request x where x.id = receipt.work_request_id for share;
    if not found
       or w.ref is distinct from p_work_request
       or receipt.base_version is distinct from p_base_version
       or receipt.disposition is distinct from p_disposition
       or receipt.fixed_surface_ref is distinct from normalized_fixed_surface
       or receipt.rationale is distinct from normalized_rationale
       or receipt.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.state is distinct from 'triaged'
       or w.version is distinct from receipt.result_version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (receipt.disposition,receipt.fixed_surface_ref,receipt.rationale,receipt.decided_by_actor_id,receipt.decided_at) then
      raise exception 'idempotency key already names a different sourced shape disposition';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
      w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;

  select x.* into w from ops.work_request x
   where x.ref = p_work_request
   for update;
  if not found
     or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'triaged'
     or w.version is distinct from p_base_version
     or w.program_key is not null or w.program_ordinal is not null
     or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
        is distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
     or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
    raise exception 'only the exact current unshaped triaged sourced Work Request may record a shape disposition';
  end if;

  insert into ops.sourced_work_request_shape_disposition_receipt
    (work_request_id,idempotency_key,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
  values
    (w.id,p_idempotency_key,p_base_version,w.version + 1,p_disposition,normalized_fixed_surface,normalized_rationale,p_decided_by_actor_id)
  returning * into receipt;

  update ops.work_request x
     set shape_disposition = receipt.disposition,
         shape_fixed_surface_ref = receipt.fixed_surface_ref,
         shape_rationale = receipt.rationale,
         shape_decided_by_actor_id = receipt.decided_by_actor_id,
         shape_decided_at = receipt.decided_at,
         version = receipt.result_version,
         updated_at = now()
   where x.id = w.id;
  select x.* into w from ops.work_request x where x.id = w.id;
  return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
    w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,false;
end;
$$;

-- Only exact receipt-backed updates are admitted.  The first branch remains
-- the original human triage.  The second is the new same-state shape mutation.
-- The final branch leaves the prior unshaped ready-plan behavior intact, while
-- letting a binding receipt preserve an already-decided required/not_required
-- disposition instead of silently rewriting it.
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
          and r.result_version = new.version and r.classification = new.triage_classification
          and r.triaged_by_actor_id = new.triaged_by_actor_id and r.triaged_at = new.triaged_at
     ) then
    return new;
  end if;

  if old.state = 'triaged'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at','version','updated_at'])
     and exists (
       select 1 from ops.sourced_work_request_shape_disposition_receipt r
        where r.work_request_id = old.id and r.base_version = old.version and r.result_version = new.version
          and (new.shape_disposition,new.shape_fixed_surface_ref,new.shape_rationale,new.shape_decided_by_actor_id,new.shape_decided_at)
             is not distinct from
             (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at)
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
         left join ops.sourced_work_request_plan_shape_binding_receipt b
           on b.plan_acceptance_receipt_id = ar.id
        where ar.work_request_id = old.id and ar.base_version = old.version
          and ar.result_version = new.version and ar.plan_hash = p.plan_hash
          and (
            (b.id is null
             and new.shape_disposition = 'not_required'
             and new.shape_fixed_surface_ref = ar.shape_fixed_surface_ref
             and new.shape_fixed_surface_ref = ('sourced-plan:' || p.plan_ref || '#' || p.plan_hash)
             and new.shape_rationale = ar.shape_rationale
             and new.shape_rationale = ('Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref || ' at sha256:' || p.runbook_content_hash)
             and new.shape_decided_by_actor_id = ar.accepted_by_actor_id
             and new.shape_decided_at = ar.accepted_at)
            or
            (b.id is not null
             and b.work_request_id = old.id
             and (new.shape_disposition,new.shape_fixed_surface_ref,new.shape_rationale,new.shape_decided_by_actor_id,new.shape_decided_at)
                 is not distinct from
                 (b.disposition,b.fixed_surface_ref,b.rationale,b.decided_by_actor_id,b.decided_at))
          )
     ) then
    return new;
  end if;

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, or triaged-to-ready transitions';
end;
$$;

-- A source row remains sourced throughout its permitted prebuild path.  The
-- generic shape-completeness constraint supplies field-level validation; this
-- constraint fixes tenant, source, and lifecycle scope without asserting that
-- every ready row took the historical automatic-not_required branch.
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
      (state = 'captured' and triage_classification is null and triaged_by_actor_id is null and triaged_at is null)
      or (state in ('triaged','ready') and triage_classification in ('operational','needs_judgment','safety_review')
          and triaged_by_actor_id is not null and triaged_at is not null)
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
     or (select array_agg(k order by k) from jsonb_object_keys(p_caps) k) is distinct from array['max_duration_minutes','max_steps']::text[]
     or coalesce(p_caps->>'max_steps','') !~ '^[0-9]+$' or coalesce(p_caps->>'max_duration_minutes','') !~ '^[0-9]+$'
     or (p_caps->>'max_steps')::integer not between 1 and 20 or (p_caps->>'max_duration_minutes')::integer not between 1 and 120 then
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
  preserve_shape boolean := false;
  applied_disposition text; applied_fixed_surface text; applied_rationale text;
  applied_actor_id uuid; applied_at timestamptz;
begin
  if p_idempotency_key is null or p_base_version is null or p_base_version < 1
     or coalesce(p_plan_hash,'') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'exact sourced plan acceptance inputs required';
  end if;
  actor_slug := ops.authority_actor_slug();
  select x.* into a from public.actor x
   where x.slug = actor_slug and x.active and x.kind = 'human' for share;
  if not found then raise exception 'authority session user is not an active human actor'; end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-plan-acceptance:' || p_idempotency_key, 0));
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.idempotency_key = p_idempotency_key for share;
  if found then
    select x.* into p from ops.sourced_work_request_plan x where x.id = ar.plan_id for share;
    select x.* into w from ops.work_request x where x.id = ar.work_request_id for share;
    if not found or w.ref is distinct from p_work_request
       or ar.base_version is distinct from p_base_version or ar.plan_hash is distinct from p_plan_hash
       or ar.accepted_by_actor_id is distinct from a.id or p.id is distinct from ar.plan_id
       or p.plan_hash is distinct from ar.plan_hash or w.state is distinct from 'ready'
       or w.version is distinct from ar.result_version
       or not exists (
         select 1 from ops.sourced_work_request_plan_shape_binding_receipt b
          where b.plan_acceptance_receipt_id = ar.id and b.work_request_id = w.id
            and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                is not distinct from (b.disposition,b.fixed_surface_ref,b.rationale,b.decided_by_actor_id,b.decided_at)
         union all
         select 1 where not exists (select 1 from ops.sourced_work_request_plan_shape_binding_receipt b where b.plan_acceptance_receipt_id = ar.id)
           and w.shape_disposition = 'not_required' and w.shape_fixed_surface_ref is not distinct from ar.shape_fixed_surface_ref
       ) then
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
  if not found or w.state is distinct from 'triaged' or w.version is distinct from p_base_version
     or p.work_request_version is distinct from p_base_version or w.capture_idempotency_key is null then
    raise exception 'exact current triaged sourced plan/version required';
  end if;

  if (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
       is not distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
     and not exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
    preserve_shape := false;
  elsif w.shape_disposition = 'required' and w.shape_fixed_surface_ref is null
     and w.shape_rationale is not null and btrim(w.shape_rationale) <> ''
     and w.shape_decided_by_actor_id is not null and w.shape_decided_at is not null
     and exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r
                  where r.work_request_id=w.id and r.result_version=w.version
                    and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                        is not distinct from (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at))
     and (select sr.work_request_version from ops.work_shape_revision sr
            where sr.work_request_id = w.id order by sr.version desc limit 1) = w.version then
    preserve_shape := true;
  elsif w.shape_disposition = 'not_required' and w.shape_fixed_surface_ref is not null
     and btrim(w.shape_fixed_surface_ref) <> '' and w.shape_rationale is not null
     and btrim(w.shape_rationale) <> '' and w.shape_decided_by_actor_id is not null
     and w.shape_decided_at is not null
     and exists (select 1 from ops.sourced_work_request_shape_disposition_receipt r
                  where r.work_request_id=w.id and r.result_version=w.version
                    and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                        is not distinct from (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at))
     and not exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
    preserve_shape := true;
  else
    raise exception 'sourced ready-plan requires either an exact unshaped request or one current receipt-backed shape disposition';
  end if;

  perform 1 from public.doctrine_document d join public.doctrine_section s on s.document_id = d.id
    join public.doctrine_revision r on r.id = s.current_revision_id and r.section_id = s.id
   where d.slug = 'runbook' and d.visibility = 'shared' and s.status = 'active'
     and s.id = p.runbook_section_id and r.id = p.runbook_revision_id and r.content_hash = p.runbook_content_hash
     and ('doctrine:' || d.slug || '#' || s.section_key) = p.runbook_ref
     and encode(public.digest(r.plain_text,'sha256'),'hex') = r.content_hash and r.body = jsonb_build_object('text',r.plain_text)
   for share of d,s,r;
  if not found then raise exception 'accepted sourced plan runbook is no longer current'; end if;
  perform 1 from public.doctrine_document source_document join public.doctrine_section source_section on source_section.document_id = source_document.id
    join public.doctrine_revision source_revision on source_revision.id = source_section.current_revision_id and source_revision.section_id = source_section.id
   where source_document.visibility = 'shared' and source_section.status = 'active'
     and source_section.id = w.doctrine_section_id and source_revision.id = w.doctrine_revision_id
     and source_revision.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(source_revision.plain_text,'sha256'),'hex') = source_revision.content_hash
     and source_revision.body = jsonb_build_object('text',source_revision.plain_text)
   for share of source_document,source_section,source_revision;
  if not found then raise exception 'accepted sourced plan source evidence is no longer current'; end if;

  canonical_preimage := ops.sourced_work_request_plan_preimage(w.id,p.scope_summary,p.runbook_ref,p.runbook_section_id,p.runbook_revision_id,p.runbook_content_hash,p.dependency_refs,p.recovery_ref,p.observability_ref,p.caps);
  canonical_hash := ops.sourced_work_request_plan_digest(canonical_preimage);
  if canonical_preimage is distinct from p.preimage or canonical_hash is distinct from p.plan_hash or canonical_hash is distinct from p_plan_hash then
    raise exception 'sourced plan preimage is stale or does not match its exact hash';
  end if;

  fixed_surface := 'sourced-plan:' || p.plan_ref || '#' || p.plan_hash;
  rationale := 'Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref || ' at sha256:' || p.runbook_content_hash;
  insert into ops.sourced_work_request_plan_acceptance_receipt
    (work_request_id,plan_id,idempotency_key,base_version,plan_hash,accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
  values (w.id,p.id,p_idempotency_key,p_base_version,p_plan_hash,a.id,w.version + 1,fixed_surface,rationale)
  returning * into ar;

  if preserve_shape then
    insert into ops.sourced_work_request_plan_shape_binding_receipt
      (plan_acceptance_receipt_id,work_request_id,disposition,fixed_surface_ref,rationale,decided_by_actor_id,decided_at)
    values (ar.id,w.id,w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at);
    applied_disposition := w.shape_disposition; applied_fixed_surface := w.shape_fixed_surface_ref;
    applied_rationale := w.shape_rationale; applied_actor_id := w.shape_decided_by_actor_id; applied_at := w.shape_decided_at;
  else
    applied_disposition := 'not_required'; applied_fixed_surface := ar.shape_fixed_surface_ref;
    applied_rationale := ar.shape_rationale; applied_actor_id := a.id; applied_at := ar.accepted_at;
  end if;

  update ops.work_request x set state='ready',version=ar.result_version,updated_at=now(),
    shape_disposition=applied_disposition,shape_fixed_surface_ref=applied_fixed_surface,
    shape_rationale=applied_rationale,shape_decided_by_actor_id=applied_actor_id,shape_decided_at=applied_at
   where x.id=w.id;
  select x.* into w from ops.work_request x where x.id=w.id;
  return query select w.id,w.ref,w.state,w.version,p.id,p.plan_ref,p.plan_hash,
    a.slug,ar.accepted_at,w.shape_disposition,w.shape_fixed_surface_ref,false;
end;
$$;

revoke all on table ops.sourced_work_request_shape_disposition_receipt,
  ops.sourced_work_request_plan_shape_binding_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)
  from public,carr_reader,carr_jobs,carr_authority;
grant execute on function ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)
  to carr_writer;

commit;
