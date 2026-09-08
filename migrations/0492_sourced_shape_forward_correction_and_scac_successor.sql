-- 0492: append-only forward correction for a sourced shape disposition.
--
-- A captured sourced request may have an immutable not_required disposition
-- which later proves incompatible with its intrinsic-heavy classification.
-- Preserve that original receipt exactly and permit one linked, monotonic
-- required correction before any Shape revision or ready-plan transition.

begin;

create table ops.sourced_work_request_shape_disposition_correction_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null unique references ops.work_request(id),
  idempotency_key uuid not null unique,
  original_receipt_id uuid not null unique references ops.sourced_work_request_shape_disposition_receipt(id),
  base_version integer not null check (base_version > 0),
  result_version integer not null check (result_version = base_version + 1),
  disposition text not null check (disposition = 'required'),
  fixed_surface_ref text check (fixed_surface_ref is null),
  rationale text not null check (btrim(rationale) <> ''),
  decided_by_actor_id uuid not null references public.actor(id),
  decided_at timestamptz not null default now()
);

comment on table ops.sourced_work_request_shape_disposition_correction_receipt is
  'Private append-only, one-time forward correction linked to an immutable sourced not_required receipt.';

create trigger sourced_work_request_shape_disposition_correction_immutable
before update or delete on ops.sourced_work_request_shape_disposition_correction_receipt
for each row execute function ops.sourced_work_shape_receipts_are_immutable();

create or replace function ops.effective_sourced_work_request_shape_disposition(p_work_request_id uuid)
returns table (
  original_receipt_id uuid, correction_receipt_id uuid, work_request_id uuid,
  disposition text, fixed_surface_ref text, rationale text,
  decided_by_actor_id uuid, decided_at timestamptz, effective_version integer
)
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
begin
  return query
  select original.id, correction.id, original.work_request_id,
    coalesce(correction.disposition, original.disposition),
    coalesce(correction.fixed_surface_ref, original.fixed_surface_ref),
    coalesce(correction.rationale, original.rationale),
    coalesce(correction.decided_by_actor_id, original.decided_by_actor_id),
    coalesce(correction.decided_at, original.decided_at),
    coalesce(correction.result_version, original.result_version)
  from ops.sourced_work_request_shape_disposition_receipt original
  left join ops.sourced_work_request_shape_disposition_correction_receipt correction
    on correction.work_request_id = original.work_request_id
   and correction.original_receipt_id = original.id
  where original.work_request_id = p_work_request_id;
  if not found then
    raise exception 'effective sourced shape disposition is unavailable';
  end if;
end;
$$;

create or replace function ops.set_sourced_work_request_shape_disposition(
  p_work_request text, p_base_version integer, p_disposition text,
  p_fixed_surface_ref text, p_rationale text, p_decided_by_actor_id uuid,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid, ref text, state text, version integer,
  shape_disposition text, shape_fixed_surface_ref text, shape_rationale text,
  shape_decided_by_actor_id uuid, shape_decided_at timestamptz, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  original ops.sourced_work_request_shape_disposition_receipt%rowtype;
  correction ops.sourced_work_request_shape_disposition_correction_receipt%rowtype;
  actor public.actor%rowtype;
  classification jsonb;
  normalized_fixed_surface text := nullif(btrim(coalesce(p_fixed_surface_ref,'')), '');
  normalized_rationale text := nullif(btrim(coalesce(p_rationale,'')), '');
begin
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_disposition not in ('required','not_required')
     or normalized_rationale is null or p_decided_by_actor_id is null or p_idempotency_key is null
     or (p_disposition='required' and normalized_fixed_surface is not null)
     or (p_disposition='not_required' and normalized_fixed_surface is null) then
    raise exception 'sourced shape disposition requires exact Work Request/base version, closed disposition, exact fixed surface rule, rationale, active actor, and UUID idempotency key';
  end if;
  select a.* into actor from public.actor a where a.id=p_decided_by_actor_id and a.active for share;
  if not found then raise exception 'sourced shape disposition actor is not active'; end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-sourced-shape-disposition:' || p_idempotency_key,0));
  select r.* into original from ops.sourced_work_request_shape_disposition_receipt r
   where r.idempotency_key=p_idempotency_key for share;
  select r.* into correction from ops.sourced_work_request_shape_disposition_correction_receipt r
   where r.idempotency_key=p_idempotency_key for share;
  if found then
    raise exception 'idempotency key already names a different sourced shape disposition';
  end if;
  if original.id is not null then
    select x.* into w from ops.work_request x where x.id=original.work_request_id for share;
    if not found or w.ref is distinct from p_work_request or original.base_version is distinct from p_base_version
       or original.disposition is distinct from p_disposition or original.fixed_surface_ref is distinct from normalized_fixed_surface
       or original.rationale is distinct from normalized_rationale or original.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.version is distinct from original.result_version then
      raise exception 'idempotency key already names a different sourced shape disposition';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;
  if correction.id is not null then
    select x.* into w from ops.work_request x where x.id=correction.work_request_id for share;
    if not found or w.ref is distinct from p_work_request or correction.base_version is distinct from p_base_version
       or p_disposition is distinct from 'required' or normalized_fixed_surface is not null
       or correction.rationale is distinct from normalized_rationale or correction.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.version is distinct from correction.result_version then
      raise exception 'idempotency key already names a different sourced shape correction';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found or w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'triaged' or w.version is distinct from p_base_version then
    raise exception 'exact current triaged sourced Work Request required';
  end if;
  select r.* into original from ops.sourced_work_request_shape_disposition_receipt r
   where r.work_request_id=w.id for share;
  if original.id is not null then
    if p_disposition is distinct from 'required' or normalized_fixed_surface is not null
       or original.disposition is distinct from 'not_required' or original.result_version is distinct from w.version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from (original.disposition,original.fixed_surface_ref,original.rationale,original.decided_by_actor_id,original.decided_at)
       or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id=w.id)
       or exists (select 1 from ops.sourced_work_request_shape_disposition_correction_receipt r where r.work_request_id=w.id) then
      raise exception 'only an exact current sourced not_required receipt may receive one required correction before Shape';
    end if;
    classification := ops.heavy_build_classification(w.id,'','[]'::jsonb,'{}'::jsonb);
    if classification is null or classification->>'tier' = 'heavy' then
      raise exception 'intrinsically heavy sourced Work Request cannot use the forward shape correction';
    end if;
    insert into ops.sourced_work_request_shape_disposition_correction_receipt
      (work_request_id,idempotency_key,original_receipt_id,base_version,result_version,rationale,decided_by_actor_id)
    values (w.id,p_idempotency_key,original.id,w.version,w.version+1,normalized_rationale,p_decided_by_actor_id)
    returning * into correction;
    update ops.work_request x set shape_disposition='required',shape_fixed_surface_ref=null,
      shape_rationale=correction.rationale,shape_decided_by_actor_id=correction.decided_by_actor_id,
      shape_decided_at=correction.decided_at,version=correction.result_version,updated_at=now() where x.id=w.id;
  else
    if exists (select 1 from ops.work_shape_revision sr where sr.work_request_id=w.id) then
      raise exception 'only the exact current unshaped triaged sourced Work Request may record a shape disposition';
    end if;
    insert into ops.sourced_work_request_shape_disposition_receipt
      (work_request_id,idempotency_key,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
    values (w.id,p_idempotency_key,p_base_version,w.version+1,p_disposition,normalized_fixed_surface,normalized_rationale,p_decided_by_actor_id)
    returning * into original;
    update ops.work_request x set shape_disposition=original.disposition,shape_fixed_surface_ref=original.fixed_surface_ref,
      shape_rationale=original.rationale,shape_decided_by_actor_id=original.decided_by_actor_id,shape_decided_at=original.decided_at,
      version=original.result_version,updated_at=now() where x.id=w.id;
  end if;
  select x.* into w from ops.work_request x where x.id=w.id;
  return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,false;
end;
$$;

revoke all on table ops.sourced_work_request_shape_disposition_correction_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)
  from public,carr_reader,carr_jobs,carr_authority;
grant execute on function ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid) to carr_writer;

commit;
