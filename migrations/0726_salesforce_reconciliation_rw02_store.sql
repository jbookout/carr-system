-- V5-RW02: append-only attended Salesforce reconciliation runtime evidence.
-- No function here contacts Salesforce or grants an external effect.

create table ops.rw02_runtime_record (
  id uuid primary key default gen_random_uuid(),
  tenant text not null check (tenant = 'carr-internal'),
  operation text not null check (operation in (
    'record-salesforce-page-stop',
    'record-salesforce-duplicate-check',
    'record-salesforce-write-readback'
  )),
  idempotency_key text not null check (length(idempotency_key) between 8 and 200),
  request_digest text not null check (request_digest ~ '^sha256:[0-9a-f]{64}$'),
  action_kind text,
  step_key text,
  outcome jsonb not null,
  record_digest text not null check (record_digest ~ '^sha256:[0-9a-f]{64}$'),
  recorded_by text not null,
  recorded_at timestamptz not null default clock_timestamp(),
  unique (operation, recorded_by, idempotency_key),
  check ((operation in ('record-salesforce-duplicate-check','record-salesforce-write-readback')) =
    (action_kind is not null and step_key is not null))
);

comment on table ops.rw02_runtime_record is
  'Append-only V5-RW02 page-stop, duplicate, and provider-readback evidence. No provider effect authority.';

revoke all on ops.rw02_runtime_record from public, carr_reader, carr_writer;

create or replace function ops.rw02_replay(
  p_operation text, p_idempotency_key text, p_request_digest text
) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare v_actor text; v_row ops.rw02_runtime_record%rowtype;
begin
  v_actor := ops.f01_context_actor_slug();
  select * into v_row from ops.rw02_runtime_record
   where operation=p_operation and recorded_by=v_actor and idempotency_key=p_idempotency_key;
  if not found then return null; end if;
  if v_row.request_digest <> p_request_digest then
    raise exception 'idempotency_key_reused';
  end if;
  return v_row.outcome || jsonb_build_object(
    'record_digest', v_row.record_digest,
    'action_kind', v_row.action_kind,
    'step_key', v_row.step_key,
    'recorded_at', to_char(v_row.recorded_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
  );
end;
$$;

create or replace function ops.rw02_record(
  p_operation text, p_idempotency_key text, p_request_digest text,
  p_action_kind text, p_step_key text, p_outcome jsonb
) returns jsonb
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_now timestamptz := clock_timestamp();
  v_outcome jsonb;
  v_digest text;
  v_row ops.rw02_runtime_record%rowtype;
  v_evidence jsonb;
begin
  if p_operation not in ('record-salesforce-page-stop','record-salesforce-duplicate-check',
                          'record-salesforce-write-readback') then
    raise exception 'rw02_operation_unregistered';
  end if;
  if p_request_digest !~ '^sha256:[0-9a-f]{64}$' then raise exception 'rw02_request_digest_invalid'; end if;
  if length(coalesce(p_idempotency_key,'')) not between 8 and 200 then
    raise exception 'rw02_idempotency_key_invalid';
  end if;
  v_actor := ops.f01_context_actor_slug();
  select * into v_row from ops.rw02_runtime_record
   where operation=p_operation and recorded_by=v_actor and idempotency_key=p_idempotency_key
   for update;
  if found then
    if v_row.request_digest <> p_request_digest then raise exception 'idempotency_key_reused'; end if;
    return v_row.outcome || jsonb_build_object('record_digest',v_row.record_digest,
      'action_kind',v_row.action_kind,'step_key',v_row.step_key);
  end if;

  v_outcome := coalesce(p_outcome,'{}'::jsonb) || jsonb_build_object(
    'schema_version','doctorcre-v5-rw02-runtime-store.v1',
    'operation',p_operation,'tenant','carr-internal','actor_slug',v_actor,
    'recorded_at',to_char(v_now at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));

  if p_operation = 'record-salesforce-write-readback' then
    v_evidence := v_outcome #> '{evaluation,evidence}';
    if v_evidence is null
       or v_evidence->>'action_kind' is distinct from p_action_kind
       or v_evidence->>'step_key' is distinct from p_step_key then
      raise exception 'rw02_evidence_binding_mismatch';
    end if;
  elsif p_operation = 'record-salesforce-duplicate-check' then
    if p_action_kind is distinct from 'opportunity_create'
       or p_step_key is null or length(p_step_key) = 0 then
      raise exception 'rw02_duplicate_binding_invalid';
    end if;
  elsif p_action_kind is not null or p_step_key is not null then
    raise exception 'rw02_unexpected_action_binding';
  end if;

  v_digest := 'sha256:' || encode(public.digest(convert_to(
    jsonb_build_object('kind','rw02-runtime-record.v1','operation',p_operation,
      'request_digest',p_request_digest,'action_kind',p_action_kind,'step_key',p_step_key,
      'outcome',v_outcome,'recorded_by',v_actor)::text, 'UTF8'), 'sha256'), 'hex');

  insert into ops.rw02_runtime_record
    (tenant,operation,idempotency_key,request_digest,action_kind,step_key,outcome,
     record_digest,recorded_by,recorded_at)
  values ('carr-internal',p_operation,p_idempotency_key,p_request_digest,p_action_kind,p_step_key,
          v_outcome,v_digest,v_actor,v_now)
  returning * into v_row;
  return v_outcome || jsonb_build_object('record_digest',v_digest,
    'action_kind',p_action_kind,'step_key',p_step_key);
end;
$$;

create or replace function ops.rw02_action_evidence(p_action_kind text)
returns table(record jsonb)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select r.outcome #> '{evaluation,evidence}'
    from ops.rw02_runtime_record r
   where r.tenant='carr-internal'
     and r.operation='record-salesforce-write-readback'
     and r.action_kind=p_action_kind
   order by r.recorded_at, r.id
$$;

revoke all on function ops.rw02_replay(text,text,text) from public;
revoke all on function ops.rw02_record(text,text,text,text,text,jsonb) from public;
revoke all on function ops.rw02_action_evidence(text) from public;
grant execute on function ops.rw02_replay(text,text,text) to carr_writer, carr_authority;
grant execute on function ops.rw02_record(text,text,text,text,text,jsonb) to carr_writer, carr_authority;
grant execute on function ops.rw02_action_evidence(text) to carr_reader, carr_writer, carr_authority;
