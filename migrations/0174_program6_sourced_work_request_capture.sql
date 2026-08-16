-- Program 6: the narrow, sourced capture lane for a canonical Work Request.
--
-- This creates only captured requests.  It does not triage, claim, dispatch,
-- approve, execute, or close work.  Existing program and historical rows are
-- untouched: the new provenance fields are nullable outside this new lane.

begin;

alter table ops.work_request
  add column if not exists organization_tenant_id text,
  add column if not exists doctrine_section_id uuid references doctrine_section(id),
  add column if not exists doctrine_revision_id uuid references doctrine_revision(id),
  add column if not exists capture_idempotency_key uuid,
  add column if not exists sourced_capture_sequence bigint;

create sequence if not exists ops.work_request_ref_seq;

alter table ops.work_request
  add constraint work_request_sourced_capture_shape check (
    (capture_idempotency_key is null and organization_tenant_id is null
      and doctrine_section_id is null
      and doctrine_revision_id is null and sourced_capture_sequence is null)
    or (capture_idempotency_key is not null and (
      organization_tenant_id = 'carr-internal'
      and doctrine_section_id is not null
      and doctrine_revision_id is not null
      and sourced_capture_sequence is not null
      and state = 'captured'
      and program_key is null and program_ordinal is null
      and origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'
    ))
  ) not valid;

create unique index if not exists work_request_capture_idempotency_key_uniq
  on ops.work_request (capture_idempotency_key)
  where capture_idempotency_key is not null;

create unique index if not exists work_request_sourced_capture_sequence_uniq
  on ops.work_request (sourced_capture_sequence)
  where sourced_capture_sequence is not null;

comment on column ops.work_request.organization_tenant_id is
  'Server-derived CARR organization tenant for sourced Program 6 capture. Historical rows remain null.';
comment on column ops.work_request.doctrine_revision_id is
  'Exact current doctrine revision that grounded a sourced capture; a stale or invented revision is refused.';

create or replace function ops.sourced_work_request_is_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, public, ops
as $$
begin
  if old.capture_idempotency_key is not null then
    raise exception 'sourced Program 6 Work Requests are captured-only and immutable in this slice';
  end if;
  return new;
end;
$$;

drop trigger if exists sourced_work_request_is_immutable on ops.work_request;
create trigger sourced_work_request_is_immutable
before update on ops.work_request
for each row execute function ops.sourced_work_request_is_immutable();

create or replace function ops.capture_sourced_work_request(
  p_origin_ref text,
  p_title text,
  p_desired_outcome text,
  p_acceptance_criteria jsonb,
  p_doctrine_section_id uuid,
  p_doctrine_revision_id uuid,
  p_idempotency_key uuid
)
returns table (
  id uuid,
  ref text,
  state text,
  version integer,
  organization_tenant_id text,
  doctrine_section_id uuid,
  doctrine_revision_id uuid,
  doctrine_source_label text,
  source_current boolean,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_section public.doctrine_section%rowtype;
  v_document public.doctrine_document%rowtype;
  v_existing ops.work_request%rowtype;
  v_sequence bigint;
  v_ref text;
  v_label text;
begin
  if p_idempotency_key is null
     or coalesce(btrim(p_origin_ref), '') !~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'
     or char_length(p_origin_ref) > 300
     or coalesce(btrim(p_title), '') = '' or char_length(p_title) > 240
     or coalesce(btrim(p_desired_outcome), '') = '' or char_length(p_desired_outcome) > 4000
     or jsonb_typeof(p_acceptance_criteria) <> 'array'
     or jsonb_array_length(p_acceptance_criteria) not between 1 and 12
     or exists (select 1 from jsonb_array_elements(p_acceptance_criteria) x
                 where jsonb_typeof(x) <> 'object' or x ?| array['id','text'] is false
                    or (select array_agg(k order by k) from jsonb_object_keys(x) k) <> array['id','text']
                    or coalesce(x->>'id','') !~ '^[A-Z][A-Z0-9-]{1,63}$'
                    or coalesce(btrim(x->>'text'),'') = '' or char_length(x->>'text') > 500) then
    raise exception 'sourced capture requires bounded exact acceptance criteria plus a doctrine origin, title, outcome, and UUID idempotency key';
  end if;
  if exists (select x->>'id' from jsonb_array_elements(p_acceptance_criteria) x
               group by x->>'id' having count(*) > 1) then
    raise exception 'sourced capture acceptance criterion IDs must be unique';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-sourced-capture:' || p_idempotency_key, 0));
  select w.* into v_existing from ops.work_request w
   where w.capture_idempotency_key=p_idempotency_key;
  if found then
    if v_existing.organization_tenant_id is distinct from 'carr-internal'
       or v_existing.origin_ref is distinct from p_origin_ref
       or v_existing.title is distinct from p_title
       or v_existing.desired_outcome is distinct from p_desired_outcome
       or v_existing.acceptance_criteria is distinct from p_acceptance_criteria
       or v_existing.doctrine_section_id is distinct from p_doctrine_section_id
       or v_existing.doctrine_revision_id is distinct from p_doctrine_revision_id then
      raise exception 'idempotency key already names a different sourced capture';
    end if;
    select coalesce(s.title, s.section_key) into v_label from public.doctrine_section s where s.id=v_existing.doctrine_section_id;
    return query select v_existing.id, v_existing.ref, v_existing.state, v_existing.version,
      v_existing.organization_tenant_id, v_existing.doctrine_section_id,
      v_existing.doctrine_revision_id, v_label,
      exists (select 1 from public.doctrine_section s where s.id=v_existing.doctrine_section_id
                and s.status='active' and s.current_revision_id=v_existing.doctrine_revision_id), true;
    return;
  end if;

  select s.* into v_section from public.doctrine_section s
   where s.id=p_doctrine_section_id
   for update;
  if not found then
    raise exception 'sourced capture requires an exact current active shared doctrine revision';
  end if;
  select d.* into v_document from public.doctrine_document d where d.id=v_section.document_id for share;
  perform 1 from public.doctrine_revision r
   where r.id=p_doctrine_revision_id and r.section_id=p_doctrine_section_id for share;
  if not found or v_section.current_revision_id is distinct from p_doctrine_revision_id
     or v_section.status <> 'active'
     or v_document.visibility <> 'shared' then
    raise exception 'sourced capture requires an exact current active shared doctrine revision';
  end if;
  if p_origin_ref is distinct from ('doctrine:' || v_document.slug || '#' || v_section.section_key) then
    raise exception 'sourced capture origin reference does not name its exact doctrine evidence';
  end if;
  if not exists (select 1 from public.doctrine_revision r where r.id=p_doctrine_revision_id and r.section_id=p_doctrine_section_id) then
    raise exception 'sourced capture doctrine evidence is invented';
  end if;

  select nextval('ops.work_request_ref_seq') into v_sequence;
  v_ref := 'WR-' || lpad(v_sequence::text, 6, '0');
  v_label := coalesce(v_section.title, v_section.section_key);
  insert into ops.work_request
    (ref,state,title,desired_outcome,acceptance_criteria,origin_ref,requester_actor,
     organization_tenant_id,doctrine_section_id,doctrine_revision_id,
     capture_idempotency_key,sourced_capture_sequence)
  values
    (v_ref,'captured',p_title,p_desired_outcome,p_acceptance_criteria,p_origin_ref,'mcp-authenticated',
     'carr-internal',p_doctrine_section_id,p_doctrine_revision_id,p_idempotency_key,v_sequence)
  returning * into v_existing;
  return query select v_existing.id, v_existing.ref, v_existing.state, v_existing.version,
    v_existing.organization_tenant_id, v_existing.doctrine_section_id,
    v_existing.doctrine_revision_id, v_label, true, false;
end;
$$;

revoke insert on ops.work_request from carr_writer;
revoke all on sequence ops.work_request_ref_seq from carr_reader, carr_writer;
revoke all on function ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid) from public;
grant execute on function ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid) to carr_writer;
comment on function ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid) is
  'The sole carr_writer INSERT path for Work Requests. Validates and locks exact current shared doctrine evidence; actor attribution remains in the MCP tool_call/event envelope.';

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
  source_current boolean
)
language sql stable security invoker
set search_path = pg_catalog, ops
as $$
  select w.ref, w.title, w.state, w.version, w.origin_ref, w.desired_outcome,
         w.acceptance_criteria, w.doctrine_section_id, w.doctrine_revision_id,
         coalesce(s.title, s.section_key) as doctrine_source_label,
         (s.status='active' and s.current_revision_id=w.doctrine_revision_id) as source_current
    from ops.work_request w
    join public.doctrine_section s on s.id=w.doctrine_section_id
    join public.doctrine_document d on d.id=s.document_id
   where p_organization_tenant_id='carr-internal'
     and w.organization_tenant_id='carr-internal'
     and w.ref=p_work_request
     and w.state='captured'
     and d.visibility='shared';
$$;

revoke all on function ops.work_request_card(text,text) from public;
grant execute on function ops.work_request_card(text,text) to carr_reader, carr_writer;

commit;
