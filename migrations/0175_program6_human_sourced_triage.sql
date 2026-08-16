-- Program 6: one human-only review transition for a sourced Work Request.
--
-- This is deliberately narrower than a general lifecycle engine.  It permits
-- only captured -> triaged, records a closed classification and reviewer, and
-- creates no assignment, dispatch, approval, execution, or later transition.

begin;

alter table ops.work_request
  add column if not exists triage_classification text,
  add column if not exists triaged_by_actor_id uuid references public.actor(id),
  add column if not exists triaged_at timestamptz;

create table if not exists ops.work_request_triage_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id),
  idempotency_key uuid not null unique,
  base_version integer not null check (base_version > 0),
  classification text not null check (classification in ('operational','needs_judgment','safety_review')),
  triaged_by_actor_id uuid not null references public.actor(id),
  result_version integer not null check (result_version > 0),
  triaged_at timestamptz not null default now(),
  unique (work_request_id)
);

comment on table ops.work_request_triage_receipt is
  'Private, append-only receipt that authorizes the sole Program 6 captured-to-triaged transition. It is not a dispatch or execution record.';

alter table ops.work_request drop constraint if exists work_request_sourced_capture_shape;
alter table ops.work_request add constraint work_request_sourced_capture_shape check (
  (capture_idempotency_key is null and organization_tenant_id is null
    and doctrine_section_id is null and doctrine_revision_id is null
    and sourced_capture_sequence is null and triage_classification is null
    and triaged_by_actor_id is null and triaged_at is null)
  or (capture_idempotency_key is not null and (
    organization_tenant_id = 'carr-internal'
    and doctrine_section_id is not null and doctrine_revision_id is not null
    and sourced_capture_sequence is not null
    and program_key is null and program_ordinal is null
    and origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'
    and (
      (state = 'captured' and triage_classification is null
       and triaged_by_actor_id is null and triaged_at is null)
      or
      (state = 'triaged' and triage_classification in ('operational','needs_judgment','safety_review')
       and triaged_by_actor_id is not null and triaged_at is not null)
    )
  ))
) not valid;

create or replace function ops.sourced_work_request_is_immutable()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops
as $$
begin
  if old.capture_idempotency_key is null then
    return new;
  end if;

  -- The receipt table has no grant for carr_writer or carr_authority.  The
  -- controlled definer function inserts this exact receipt before its update;
  -- direct table DML therefore cannot manufacture a sourced transition.
  if old.state = 'captured'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
     and exists (
       select 1 from ops.work_request_triage_receipt r
        where r.work_request_id = old.id
          and r.base_version = old.version
          and r.result_version = new.version
          and r.classification = new.triage_classification
          and r.triaged_by_actor_id = new.triaged_by_actor_id
          and r.triaged_at = new.triaged_at
     ) then
    return new;
  end if;
  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged review';
end;
$$;

create or replace function ops.triage_sourced_work_request(
  p_work_request text,
  p_base_version integer,
  p_classification text,
  p_idempotency_key uuid
)
returns table (
  id uuid,
  ref text,
  state text,
  version integer,
  classification text,
  triaged_by_actor_slug text,
  triaged_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor_slug text;
  v_actor public.actor%rowtype;
  v_work_request ops.work_request%rowtype;
  v_receipt ops.work_request_triage_receipt%rowtype;
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_classification is null
     or p_classification not in ('operational','needs_judgment','safety_review')
     or p_idempotency_key is null then
    raise exception 'human triage requires a Work Request ref, exact positive base version, closed classification, and UUID idempotency key';
  end if;

  v_actor_slug := ops.authority_actor_slug();
  select a.* into v_actor from public.actor a
   where a.slug = v_actor_slug and a.active and a.kind = 'human'
   for share;
  if not found then
    raise exception 'authority session user is not an active human actor';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-human-triage:' || p_idempotency_key, 0));
  select r.* into v_receipt from ops.work_request_triage_receipt r
   where r.idempotency_key = p_idempotency_key
   for share;
  if found then
    select w.* into v_work_request from ops.work_request w where w.id = v_receipt.work_request_id for share;
    if not found
       or v_receipt.base_version is distinct from p_base_version
       or v_receipt.classification is distinct from p_classification
       or v_receipt.triaged_by_actor_id is distinct from v_actor.id
       or v_work_request.ref is distinct from p_work_request
       or v_work_request.state is distinct from 'triaged'
       or v_work_request.version is distinct from v_receipt.result_version then
      raise exception 'idempotency key already names a different human triage';
    end if;
    return query select v_work_request.id, v_work_request.ref, v_work_request.state,
      v_work_request.version, v_receipt.classification, v_actor.slug, v_receipt.triaged_at, true;
    return;
  end if;

  select w.* into v_work_request from ops.work_request w
   where w.ref = p_work_request
   for update;
  if not found
     or v_work_request.capture_idempotency_key is null
     or v_work_request.organization_tenant_id is distinct from 'carr-internal'
     or v_work_request.state is distinct from 'captured'
     or v_work_request.version is distinct from p_base_version
     or v_work_request.program_key is not null or v_work_request.program_ordinal is not null then
    raise exception 'only the exact current captured sourced Work Request may be triaged';
  end if;

  insert into ops.work_request_triage_receipt
    (work_request_id,idempotency_key,base_version,classification,triaged_by_actor_id,result_version)
  values
    (v_work_request.id,p_idempotency_key,p_base_version,p_classification,v_actor.id,v_work_request.version + 1)
  returning * into v_receipt;

  update ops.work_request w
     set state = 'triaged',
         triage_classification = p_classification,
         triaged_by_actor_id = v_actor.id,
         triaged_at = v_receipt.triaged_at,
         version = v_receipt.result_version,
         updated_at = now()
   where w.id = v_work_request.id;
  select w.* into v_work_request from ops.work_request w where w.id = v_work_request.id;
  return query select v_work_request.id, v_work_request.ref, v_work_request.state,
    v_work_request.version, v_receipt.classification, v_actor.slug, v_receipt.triaged_at, false;
end;
$$;

revoke all on table ops.work_request_triage_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;
revoke all on function ops.triage_sourced_work_request(text,integer,text,uuid) from public, carr_reader, carr_writer, carr_jobs;
grant execute on function ops.triage_sourced_work_request(text,integer,text,uuid) to carr_authority;

comment on function ops.triage_sourced_work_request(text,integer,text,uuid) is
  'Human-only Program 6 review. The reviewer is derived from session_user through authority_actor_slug(); no caller actor, tenant, state, assignment, dispatch, approval, or execution field is accepted.';

-- The migration graph has no SQL object dependent on the 0174 card (only the
-- MCP caller); PostgreSQL cannot replace a function while changing OUT shape.
drop function ops.work_request_card(text,text);

create or replace function ops.work_request_card(
  p_work_request text,
  p_organization_tenant_id text
)
returns table (
  ref text,
  title text,
  state text,
  version integer,
  origin_ref text,
  desired_outcome text,
  acceptance_criteria jsonb,
  doctrine_section_id uuid,
  doctrine_revision_id uuid,
  doctrine_source_label text,
  source_current boolean,
  triage_classification text,
  triaged_by_actor_slug text,
  triaged_at timestamptz
)
language sql stable security invoker
set search_path = pg_catalog, ops
as $$
  select w.ref, w.title, w.state, w.version, w.origin_ref, w.desired_outcome,
         w.acceptance_criteria, w.doctrine_section_id, w.doctrine_revision_id,
         coalesce(s.title, s.section_key) as doctrine_source_label,
         (s.status='active' and s.current_revision_id=w.doctrine_revision_id) as source_current,
         w.triage_classification, a.slug as triaged_by_actor_slug, w.triaged_at
    from ops.work_request w
    join public.doctrine_section s on s.id=w.doctrine_section_id
    join public.doctrine_document d on d.id=s.document_id
    left join public.actor a on a.id=w.triaged_by_actor_id
   where p_organization_tenant_id='carr-internal'
     and w.organization_tenant_id='carr-internal'
     and w.ref=p_work_request
     and w.state in ('captured','triaged')
     and d.visibility='shared';
$$;

revoke all on function ops.work_request_card(text,text) from public;
grant execute on function ops.work_request_card(text,text) to carr_reader, carr_writer;

comment on function ops.work_request_card(text,text) is
  'Same-tenant shared-source Program 6 card. It exposes captured/triaged provenance and reviewer metadata only; later lifecycle states remain absent.';

commit;
