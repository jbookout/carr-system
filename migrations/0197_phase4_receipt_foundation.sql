-- 0197_phase4_receipt_foundation.sql
-- Phase 4 predecessor only: typed immutable source/receiver receipts and a
-- separate whole-Drive retirement authority receipt.  This migration does not
-- reduce a continuity window, accept Phase 4, or retire Drive.

begin;

create table ops.phase4_tenant_contract (
  tenant_id text primary key check (tenant_id='carr-internal'),
  canonical_domain text not null unique check (canonical_domain='carr.us'),
  created_at timestamptz not null default now()
);
insert into ops.phase4_tenant_contract(tenant_id,canonical_domain)
values ('carr-internal','carr.us');

-- Governance guard, independent of evidence volume: continuity/adoption is an
-- optional compatibility observation.  It is never an approval rail.  Joe is
-- the sole required system authority and no Dell receipt may gate design,
-- rollout, activation, or a high-level system decision.
create table ops.phase4_system_authority_contract (
  contract_key text primary key check (contract_key='phase4_optional_continuity_v1'),
  sole_required_system_authority text not null check (sole_required_system_authority='joe'),
  dell_participation text not null check (dell_participation='optional_nonblocking'),
  continuity_may_gate_system_rollout boolean not null check (not continuity_may_gate_system_rollout),
  continuity_may_gate_system_activation boolean not null check (not continuity_may_gate_system_activation),
  created_at timestamptz not null default now()
);
insert into ops.phase4_system_authority_contract
  (contract_key,sole_required_system_authority,dell_participation,
   continuity_may_gate_system_rollout,continuity_may_gate_system_activation)
values ('phase4_optional_continuity_v1','joe','optional_nonblocking',false,false);

create table ops.phase4_actor_tenant (
  actor_slug text primary key check (actor_slug in ('joe','dell')),
  tenant_id text not null default 'carr-internal',
  canonical_domain text not null default 'carr.us',
  foreign key (actor_slug) references actor(slug) on delete restrict,
  foreign key (tenant_id) references ops.phase4_tenant_contract(tenant_id) on delete restrict,
  foreign key (canonical_domain) references ops.phase4_tenant_contract(canonical_domain) on delete restrict,
  unique(actor_slug,tenant_id)
);
insert into ops.phase4_actor_tenant(actor_slug) values ('joe'),('dell');

-- Existing device principals deliberately do not carry a human or tenant.
-- This owner-provisioned binding supplies both without trusting a device call.
alter table ops.device_evidence_principal
  add constraint device_evidence_principal_login_device_unique unique(login_role,device_id);
create table ops.phase4_device_partner_binding (
  login_role name primary key,
  device_id text not null unique,
  actor_slug text not null,
  tenant_id text not null,
  foreign key (login_role,device_id)
    references ops.device_evidence_principal(login_role,device_id) on delete restrict,
  foreign key (actor_slug,tenant_id)
    references ops.phase4_actor_tenant(actor_slug,tenant_id) on delete restrict
);

create table ops.phase4_read_principal (
  login_role name primary key check (login_role in ('carr_reader','carr_jobs')),
  tenant_id text not null references ops.phase4_tenant_contract(tenant_id) on delete restrict
);
insert into ops.phase4_read_principal(login_role,tenant_id)
values ('carr_reader','carr-internal'),('carr_jobs','carr-internal');

-- A receipt session is minted from the authenticated PostgreSQL backend, not
-- supplied by a caller.  backend_start prevents PID reuse from merging runs.
create table ops.phase4_runtime_session (
  id uuid primary key default gen_random_uuid(),
  login_role name not null,
  backend_pid integer not null,
  backend_start timestamptz not null,
  actor_slug text not null,
  tenant_id text not null,
  created_at timestamptz not null default now(),
  foreign key (actor_slug,tenant_id)
    references ops.phase4_actor_tenant(actor_slug,tenant_id) on delete restrict,
  unique(login_role,backend_pid,backend_start,actor_slug)
);

-- The pre-existing retrieval log had result hashes/IDs but no structural link
-- to the sponsored read audit that produced them.  Add a nullable predecessor
-- link: historical/unwired rows remain valid history, but Phase 4 receipts
-- refuse them until the canonical retrieval runtime writes all three fields.
alter table retrieval_query_log
  add column phase4_tool_read_call_id uuid references tool_read_call(id) on delete restrict,
  add column phase4_actor_slug text,
  add column phase4_tenant_id text,
  add constraint retrieval_query_log_phase4_actor_tenant_fk
    foreign key (phase4_actor_slug,phase4_tenant_id)
    references ops.phase4_actor_tenant(actor_slug,tenant_id) on delete restrict,
  add constraint retrieval_query_log_phase4_binding_complete check (
    (phase4_tool_read_call_id is null and phase4_actor_slug is null and phase4_tenant_id is null)
    or (phase4_tool_read_call_id is not null and phase4_actor_slug is not null and phase4_tenant_id is not null)
  );

create table ops.phase4_source_receipt (
  id uuid primary key,
  receipt_ref text not null unique,
  stream text not null check (stream in (
    'standing_context','governed_retrieval','tentative_write_readback',
    'conflict_undo','personal_canary_privacy_model_telemetry','document_download')),
  actor_slug text not null,
  tenant_id text not null,
  source_session_id uuid not null references ops.phase4_runtime_session(id) on delete restrict,
  evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  minted_at timestamptz not null default now(),
  foreign key (actor_slug,tenant_id)
    references ops.phase4_actor_tenant(actor_slug,tenant_id) on delete restrict,
  unique(id,stream)
);

create table ops.phase4_standing_context_source (
  receipt_id uuid primary key,
  stream text not null default 'standing_context' check (stream='standing_context'),
  tool_read_call_id uuid not null unique references tool_read_call(id) on delete restrict,
  foreign key (receipt_id,stream) references ops.phase4_source_receipt(id,stream) on delete restrict
);
create table ops.phase4_governed_retrieval_source (
  receipt_id uuid primary key,
  stream text not null default 'governed_retrieval' check (stream='governed_retrieval'),
  tool_read_call_id uuid not null references tool_read_call(id) on delete restrict,
  retrieval_query_log_id uuid not null references retrieval_query_log(id) on delete restrict,
  foreign key (receipt_id,stream) references ops.phase4_source_receipt(id,stream) on delete restrict,
  unique(tool_read_call_id,retrieval_query_log_id)
);
create table ops.phase4_tentative_write_source (
  receipt_id uuid primary key,
  stream text not null default 'tentative_write_readback' check (stream='tentative_write_readback'),
  tool_call_idempotency_key text not null references tool_call(idempotency_key) on delete restrict,
  readback_event_id uuid not null unique references event(id) on delete restrict,
  foreign key (receipt_id,stream) references ops.phase4_source_receipt(id,stream) on delete restrict
);
create table ops.phase4_conflict_source (
  receipt_id uuid primary key,
  stream text not null default 'conflict_undo' check (stream='conflict_undo'),
  first_proposal_id uuid not null references retrieval_proposal(id) on delete restrict,
  second_proposal_id uuid not null references retrieval_proposal(id) on delete restrict,
  first_event_id uuid not null references event(id) on delete restrict,
  second_event_id uuid not null references event(id) on delete restrict,
  foreign key (receipt_id,stream) references ops.phase4_source_receipt(id,stream) on delete restrict,
  check (first_proposal_id<>second_proposal_id and first_event_id<>second_event_id),
  unique(first_proposal_id,second_proposal_id,first_event_id,second_event_id)
);
create table ops.phase4_privacy_scan_source (
  receipt_id uuid primary key,
  stream text not null default 'personal_canary_privacy_model_telemetry'
    check (stream='personal_canary_privacy_model_telemetry'),
  job_receipt_id uuid not null unique references ops.job_receipt(id) on delete restrict,
  foreign key (receipt_id,stream) references ops.phase4_source_receipt(id,stream) on delete restrict
);
create table ops.phase4_document_download_source (
  receipt_id uuid primary key,
  stream text not null default 'document_download' check (stream='document_download'),
  job_receipt_id uuid not null unique references ops.job_receipt(id) on delete restrict,
  attachment_id uuid not null references attachment(id) on delete restrict,
  foreign key (receipt_id,stream) references ops.phase4_source_receipt(id,stream) on delete restrict,
  unique(job_receipt_id,attachment_id)
);

create table ops.phase4_receiver_receipt (
  id uuid primary key default gen_random_uuid(),
  receipt_ref text not null unique,
  source_receipt_id uuid not null references ops.phase4_source_receipt(id) on delete restrict,
  source_actor_slug text not null,
  receiver_actor_slug text not null,
  tenant_id text not null,
  device_id text not null,
  receiver_session_id uuid not null references ops.phase4_runtime_session(id) on delete restrict,
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  received_at timestamptz not null default now(),
  foreign key (source_actor_slug,tenant_id)
    references ops.phase4_actor_tenant(actor_slug,tenant_id) on delete restrict,
  foreign key (receiver_actor_slug,tenant_id)
    references ops.phase4_actor_tenant(actor_slug,tenant_id) on delete restrict,
  foreign key (device_id) references ops.device_evidence_principal(device_id) on delete restrict,
  check (source_actor_slug<>receiver_actor_slug),
  unique(source_receipt_id,device_id,receiver_session_id)
);

create table ops.phase4_drive_evidence_receipt (
  id uuid primary key default gen_random_uuid(),
  receipt_ref text not null unique,
  evidence_kind text not null check (evidence_kind in ('inventory','repoint','recovery','cutover')),
  tenant_id text not null references ops.phase4_tenant_contract(tenant_id) on delete restrict,
  source_job_receipt_id uuid not null unique references ops.job_receipt(id) on delete restrict,
  evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  minted_at timestamptz not null default now(),
  unique(id,evidence_kind)
);

create table ops.phase4_drive_retirement_authority_receipt (
  id uuid primary key default gen_random_uuid(),
  receipt_ref text not null unique,
  tenant_id text not null references ops.phase4_tenant_contract(tenant_id) on delete restrict,
  approved_by text not null check (approved_by='joe'),
  inventory_receipt_id uuid not null,
  inventory_kind text not null default 'inventory' check (inventory_kind='inventory'),
  repoint_receipt_id uuid not null,
  repoint_kind text not null default 'repoint' check (repoint_kind='repoint'),
  recovery_receipt_id uuid not null,
  recovery_kind text not null default 'recovery' check (recovery_kind='recovery'),
  cutover_receipt_id uuid not null,
  cutover_kind text not null default 'cutover' check (cutover_kind='cutover'),
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  approved_at timestamptz not null default now(),
  foreign key (inventory_receipt_id,inventory_kind)
    references ops.phase4_drive_evidence_receipt(id,evidence_kind) on delete restrict,
  foreign key (repoint_receipt_id,repoint_kind)
    references ops.phase4_drive_evidence_receipt(id,evidence_kind) on delete restrict,
  foreign key (recovery_receipt_id,recovery_kind)
    references ops.phase4_drive_evidence_receipt(id,evidence_kind) on delete restrict,
  foreign key (cutover_receipt_id,cutover_kind)
    references ops.phase4_drive_evidence_receipt(id,evidence_kind) on delete restrict,
  unique(inventory_receipt_id,repoint_receipt_id,recovery_receipt_id,cutover_receipt_id),
  check (inventory_receipt_id<>repoint_receipt_id and inventory_receipt_id<>recovery_receipt_id
     and inventory_receipt_id<>cutover_receipt_id and repoint_receipt_id<>recovery_receipt_id
     and repoint_receipt_id<>cutover_receipt_id and recovery_receipt_id<>cutover_receipt_id)
);

create or replace function ops.phase4_refuse_rewrite()
returns trigger language plpgsql as $$
begin raise exception '% is append-only',tg_table_name; end $$;

do $$ declare relation regclass; trigger_name text; begin
  foreach relation in array array[
    'ops.phase4_runtime_session'::regclass,'ops.phase4_source_receipt'::regclass,
    'ops.phase4_standing_context_source'::regclass,'ops.phase4_governed_retrieval_source'::regclass,
    'ops.phase4_tentative_write_source'::regclass,'ops.phase4_conflict_source'::regclass,
    'ops.phase4_privacy_scan_source'::regclass,'ops.phase4_document_download_source'::regclass,
    'ops.phase4_receiver_receipt'::regclass,'ops.phase4_drive_evidence_receipt'::regclass,
    'ops.phase4_drive_retirement_authority_receipt'::regclass
  ] loop
    trigger_name := replace(relation::text,'.','_') || '_append_only';
    execute format('create trigger %I before update or delete on %s for each row execute function ops.phase4_refuse_rewrite()',trigger_name,relation);
  end loop;
end $$;

create or replace function ops.phase4_digest(p_value jsonb)
returns text language sql immutable strict as $$
  select encode(digest(convert_to(p_value::text,'utf8'),'sha256'),'hex')
$$;

create or replace function ops.phase4_lock_idempotency(p_scope text,p_idempotency_key text)
returns void language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if btrim(coalesce(p_scope,''))='' or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'Phase 4 idempotency scope and key are required';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_scope||':'||p_idempotency_key,4197));
end $$;

create or replace function ops.phase4_server_session(p_actor_slug text)
returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare started timestamptz; session_id uuid; tenant text;
begin
  select backend_start into started from pg_stat_activity where pid=pg_backend_pid();
  if started is null then raise exception 'Phase 4 runtime backend identity is unavailable'; end if;
  select tenant_id into tenant from ops.phase4_actor_tenant where actor_slug=p_actor_slug;
  if tenant is null then raise exception 'Phase 4 actor is not bound to the canonical tenant'; end if;
  select id into session_id from ops.phase4_runtime_session
   where login_role=session_user and backend_pid=pg_backend_pid()
     and backend_start=started and actor_slug=p_actor_slug;
  if session_id is null then
    insert into ops.phase4_runtime_session(login_role,backend_pid,backend_start,actor_slug,tenant_id)
    values(session_user,pg_backend_pid(),started,p_actor_slug,tenant) returning id into session_id;
  end if;
  return session_id;
end $$;

create or replace function ops.phase4_insert_source(
  p_stream text,p_actor_slug text,p_evidence jsonb,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare result_id uuid:=gen_random_uuid(); tenant text; session_id uuid;
begin
  if not pg_has_role(session_user,'carr_writer','member') then
    raise exception 'Phase 4 source receipts require the canonical runtime writer';
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' then raise exception 'Phase 4 idempotency key is required'; end if;
  select tenant_id into tenant from ops.phase4_actor_tenant where actor_slug=p_actor_slug;
  if tenant is null then raise exception 'Phase 4 source actor is not canonical'; end if;
  session_id:=ops.phase4_server_session(p_actor_slug);
  insert into ops.phase4_source_receipt
    (id,receipt_ref,stream,actor_slug,tenant_id,source_session_id,evidence_sha256,idempotency_key)
  values(result_id,'phase4-source:'||result_id,p_stream,p_actor_slug,tenant,session_id,
         ops.phase4_digest(p_evidence),p_idempotency_key);
  return result_id;
end $$;

create or replace function ops.record_phase4_standing_context(
  p_tool_read_call_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare call_row tool_read_call%rowtype; existing uuid; bound uuid; actor_slug text;
begin
  perform ops.phase4_lock_idempotency('source',p_idempotency_key);
  select r.id,s.tool_read_call_id into existing,bound
    from ops.phase4_source_receipt r join ops.phase4_standing_context_source s on s.receipt_id=r.id
   where r.idempotency_key=p_idempotency_key;
  if existing is not null then
    if bound is distinct from p_tool_read_call_id then raise exception 'Phase 4 standing-context idempotency mismatch'; end if;
    return existing;
  end if;
  select * into call_row from tool_read_call where id=p_tool_read_call_id for share;
  actor_slug:=call_row.sponsoring_human_slug;
  if not found or not call_row.ok or call_row.verb<>'standing-context'
     or actor_slug not in ('joe','dell') or call_row.actor_slug<>actor_slug then
    raise exception 'Phase 4 standing-context source is not an accepted sponsored canonical read audit';
  end if;
  existing:=ops.phase4_insert_source('standing_context',actor_slug,to_jsonb(call_row),p_idempotency_key);
  insert into ops.phase4_standing_context_source(receipt_id,tool_read_call_id)
  values(existing,p_tool_read_call_id);
  return existing;
end $$;

create or replace function ops.record_phase4_governed_retrieval(
  p_tool_read_call_id uuid,p_retrieval_query_log_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare call_row tool_read_call%rowtype; query_row retrieval_query_log%rowtype;
        existing uuid; bound_call uuid; bound_query uuid; actor_slug text;
begin
  perform ops.phase4_lock_idempotency('source',p_idempotency_key);
  select r.id,s.tool_read_call_id,s.retrieval_query_log_id into existing,bound_call,bound_query
    from ops.phase4_source_receipt r join ops.phase4_governed_retrieval_source s on s.receipt_id=r.id
   where r.idempotency_key=p_idempotency_key;
  if existing is not null then
    if bound_call is distinct from p_tool_read_call_id or bound_query is distinct from p_retrieval_query_log_id then
      raise exception 'Phase 4 governed-retrieval idempotency mismatch';
    end if; return existing;
  end if;
  select * into call_row from tool_read_call where id=p_tool_read_call_id for share;
  if not found then raise exception 'Phase 4 governed retrieval lacks its read audit'; end if;
  actor_slug:=call_row.sponsoring_human_slug;
  select * into query_row from retrieval_query_log where id=p_retrieval_query_log_id for share;
  if not found or not call_row.ok or call_row.verb not in ('search-doctrine','situation-retrieval')
     or actor_slug not in ('joe','dell') or call_row.actor_slug<>actor_slug
     or query_row.phase4_tool_read_call_id is distinct from call_row.id
     or query_row.phase4_actor_slug is distinct from actor_slug
     or query_row.phase4_tenant_id is distinct from 'carr-internal' then
    raise exception 'Phase 4 governed retrieval does not bind an actual result audit to its sponsored read';
  end if;
  existing:=ops.phase4_insert_source('governed_retrieval',actor_slug,
    jsonb_build_object('read',to_jsonb(call_row),'result_audit',to_jsonb(query_row)),p_idempotency_key);
  insert into ops.phase4_governed_retrieval_source(receipt_id,tool_read_call_id,retrieval_query_log_id)
  values(existing,p_tool_read_call_id,p_retrieval_query_log_id);
  return existing;
end $$;

create or replace function ops.record_phase4_tentative_write_readback(
  p_tool_call_idempotency_key text,p_readback_event_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tool_row tool_call%rowtype; event_row event%rowtype; existing uuid; bound_key text; bound_event uuid; actor_slug text;
begin
  perform ops.phase4_lock_idempotency('source',p_idempotency_key);
  select r.id,s.tool_call_idempotency_key,s.readback_event_id into existing,bound_key,bound_event
    from ops.phase4_source_receipt r join ops.phase4_tentative_write_source s on s.receipt_id=r.id
   where r.idempotency_key=p_idempotency_key;
  if existing is not null then
    if bound_key is distinct from p_tool_call_idempotency_key or bound_event is distinct from p_readback_event_id then
      raise exception 'Phase 4 write/readback idempotency mismatch';
    end if; return existing;
  end if;
  select * into tool_row from tool_call where idempotency_key=p_tool_call_idempotency_key for share;
  if not found then raise exception 'Phase 4 write/readback lacks its governed tool call'; end if;
  select * into event_row from event where id=p_readback_event_id for share;
  select slug into actor_slug from actor where id=tool_row.actor_id and kind='human' and active;
  if not found or actor_slug not in ('joe','dell') or event_row.actor_id is distinct from tool_row.actor_id
     or event_row.idempotency_key is distinct from tool_row.idempotency_key
     or jsonb_typeof(tool_row.response)<>'object' then
    raise exception 'Phase 4 write/readback source is not one actual governed mutation and event readback';
  end if;
  existing:=ops.phase4_insert_source('tentative_write_readback',actor_slug,
    jsonb_build_object('tool_call',to_jsonb(tool_row),'readback_event',to_jsonb(event_row)),p_idempotency_key);
  insert into ops.phase4_tentative_write_source(receipt_id,tool_call_idempotency_key,readback_event_id)
  values(existing,p_tool_call_idempotency_key,p_readback_event_id);
  return existing;
end $$;

create or replace function ops.record_phase4_conflict_undo(
  p_first_proposal_id uuid,p_second_proposal_id uuid,p_first_event_id uuid,p_second_event_id uuid,
  p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare p1 retrieval_proposal%rowtype; p2 retrieval_proposal%rowtype; e1 event%rowtype; e2 event%rowtype;
        existing uuid; b1 uuid; b2 uuid; b3 uuid; b4 uuid; actor_slug text;
begin
  perform ops.phase4_lock_idempotency('source',p_idempotency_key);
  select r.id,s.first_proposal_id,s.second_proposal_id,s.first_event_id,s.second_event_id
    into existing,b1,b2,b3,b4 from ops.phase4_source_receipt r
    join ops.phase4_conflict_source s on s.receipt_id=r.id where r.idempotency_key=p_idempotency_key;
  if existing is not null then
    if row(b1,b2,b3,b4) is distinct from row(p_first_proposal_id,p_second_proposal_id,p_first_event_id,p_second_event_id) then
      raise exception 'Phase 4 conflict/undo idempotency mismatch';
    end if; return existing;
  end if;
  select * into p1 from retrieval_proposal where id=p_first_proposal_id for share;
  if not found then raise exception 'Phase 4 conflict/undo lacks first proposal'; end if;
  select * into p2 from retrieval_proposal where id=p_second_proposal_id for share;
  select * into e1 from event where id=p_first_event_id for share;
  select * into e2 from event where id=p_second_event_id for share;
  select slug into actor_slug from actor where id=p1.proposer_id and kind='human' and active;
  if p2.id is null or e1.id is null or e2.id is null or actor_slug not in ('joe','dell')
     or p1.id=p2.id or p1.proposer_id<>p2.proposer_id
     or p1.status='pending' or p2.status='pending' or e1.id=e2.id
     or e1.actor_id<>p1.proposer_id or e2.actor_id<>p2.proposer_id
     or e1.subject_type<>'retrieval_proposal' or e2.subject_type<>'retrieval_proposal'
     or e1.subject_id<>p1.id or e2.subject_id<>p2.id then
    raise exception 'Phase 4 conflict/undo must bind both resolved proposals and both governed events';
  end if;
  existing:=ops.phase4_insert_source('conflict_undo',actor_slug,
    jsonb_build_object('first_proposal',to_jsonb(p1),'second_proposal',to_jsonb(p2),
                       'first_event',to_jsonb(e1),'second_event',to_jsonb(e2)),p_idempotency_key);
  insert into ops.phase4_conflict_source
    (receipt_id,first_proposal_id,second_proposal_id,first_event_id,second_event_id)
  values(existing,p1.id,p2.id,e1.id,e2.id);
  return existing;
end $$;

create or replace function ops.record_phase4_privacy_scan(
  p_job_receipt_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare receipt_row ops.job_receipt%rowtype; job_row ops.job%rowtype; existing uuid; bound uuid; actor_slug text;
begin
  perform ops.phase4_lock_idempotency('source',p_idempotency_key);
  select r.id,s.job_receipt_id into existing,bound from ops.phase4_source_receipt r
    join ops.phase4_privacy_scan_source s on s.receipt_id=r.id where r.idempotency_key=p_idempotency_key;
  if existing is not null then
    if bound is distinct from p_job_receipt_id then raise exception 'Phase 4 privacy-scan idempotency mismatch'; end if;
    return existing;
  end if;
  select * into receipt_row from ops.job_receipt where id=p_job_receipt_id for share;
  if not found then raise exception 'Phase 4 privacy scan lacks its completion audit'; end if;
  select * into job_row from ops.job where id=receipt_row.job_id for share;
  select owner_actor into actor_slug from ops.job_definition
   where key=job_row.definition_key and version=job_row.definition_version;
  if job_row.id is null or actor_slug is null or receipt_row.kind<>'completion' or job_row.state<>'succeeded'
     or job_row.definition_key<>'phase4-personal-canary-scan' or actor_slug not in ('joe','dell')
     or receipt_row.evidence<>jsonb_build_object('privacy_scan',true,'model_output_scan',true,'telemetry_scan',true) then
    raise exception 'Phase 4 personal canary must bind the actual privacy/model-output/telemetry completion audit';
  end if;
  existing:=ops.phase4_insert_source('personal_canary_privacy_model_telemetry',actor_slug,
    jsonb_build_object('job',to_jsonb(job_row),'receipt',to_jsonb(receipt_row)),p_idempotency_key);
  insert into ops.phase4_privacy_scan_source(receipt_id,job_receipt_id) values(existing,p_job_receipt_id);
  return existing;
end $$;

create or replace function ops.record_phase4_document_download(
  p_job_receipt_id uuid,p_attachment_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare receipt_row ops.job_receipt%rowtype; job_row ops.job%rowtype; attachment_row attachment%rowtype;
        existing uuid; bound_job uuid; bound_attachment uuid; actor_slug text;
begin
  perform ops.phase4_lock_idempotency('source',p_idempotency_key);
  select r.id,s.job_receipt_id,s.attachment_id into existing,bound_job,bound_attachment
    from ops.phase4_source_receipt r join ops.phase4_document_download_source s on s.receipt_id=r.id
   where r.idempotency_key=p_idempotency_key;
  if existing is not null then
    if bound_job is distinct from p_job_receipt_id or bound_attachment is distinct from p_attachment_id then
      raise exception 'Phase 4 document-download idempotency mismatch';
    end if; return existing;
  end if;
  select * into receipt_row from ops.job_receipt where id=p_job_receipt_id for share;
  if not found then raise exception 'Phase 4 document download lacks its completion audit'; end if;
  select * into job_row from ops.job where id=receipt_row.job_id for share;
  select * into attachment_row from attachment where id=p_attachment_id and deleted_at is null for share;
  select owner_actor into actor_slug from ops.job_definition
   where key=job_row.definition_key and version=job_row.definition_version;
  if job_row.id is null or attachment_row.id is null or actor_slug is null
     or receipt_row.kind<>'completion' or job_row.state<>'succeeded'
     or job_row.definition_key<>'phase4-document-download' or actor_slug not in ('joe','dell')
     or receipt_row.evidence<>jsonb_build_object('attachment_id',p_attachment_id::text,
          'fetched_bytes_sha256',attachment_row.sha256,'download_audit',true) then
    raise exception 'Phase 4 document receipt must bind actual fetched bytes and its immutable download audit';
  end if;
  existing:=ops.phase4_insert_source('document_download',actor_slug,
    jsonb_build_object('job',to_jsonb(job_row),'receipt',to_jsonb(receipt_row),'attachment',to_jsonb(attachment_row)),
    p_idempotency_key);
  insert into ops.phase4_document_download_source(receipt_id,job_receipt_id,attachment_id)
  values(existing,p_job_receipt_id,p_attachment_id);
  return existing;
end $$;

create or replace function ops.receive_phase4_source_receipt(
  p_source_receipt_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare principal ops.device_evidence_principal%rowtype; binding ops.phase4_device_partner_binding%rowtype;
        source ops.phase4_source_receipt%rowtype; existing ops.phase4_receiver_receipt%rowtype;
        session_id uuid; result_id uuid:=gen_random_uuid();
begin
  perform ops.phase4_lock_idempotency('receiver',p_idempotency_key);
  select * into principal from ops.device_evidence_principal where login_role=session_user and active;
  select * into binding from ops.phase4_device_partner_binding
   where login_role=session_user and device_id=principal.device_id;
  if principal.login_role is null or binding.login_role is null then
    raise exception 'Phase 4 receiver is not an active partner/tenant-bound device principal';
  end if;
  session_id:=ops.phase4_server_session(binding.actor_slug);
  select * into existing from ops.phase4_receiver_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.source_receipt_id is distinct from p_source_receipt_id
       or existing.receiver_actor_slug is distinct from binding.actor_slug
       or existing.device_id is distinct from binding.device_id
       or existing.receiver_session_id is distinct from session_id then
      raise exception 'Phase 4 receiver idempotency key is bound to a different receipt or server session';
    end if; return existing.id;
  end if;
  select * into source from ops.phase4_source_receipt where id=p_source_receipt_id for share;
  if not found or source.tenant_id<>binding.tenant_id or source.actor_slug=binding.actor_slug then
    raise exception 'Phase 4 receiver requires a pre-existing same-tenant cross-partner server receipt';
  end if;
  insert into ops.phase4_receiver_receipt
    (id,receipt_ref,source_receipt_id,source_actor_slug,receiver_actor_slug,tenant_id,
     device_id,receiver_session_id,idempotency_key)
  values(result_id,'phase4-receiver:'||result_id,source.id,source.actor_slug,binding.actor_slug,
         binding.tenant_id,binding.device_id,session_id,p_idempotency_key);
  return result_id;
end $$;

create or replace function ops.phase4_insert_drive_evidence(
  p_job_receipt_id uuid,p_evidence_kind text,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare receipt_row ops.job_receipt%rowtype; job_row ops.job%rowtype; existing ops.phase4_drive_evidence_receipt%rowtype;
        expected_key text; expected_evidence jsonb; result_id uuid:=gen_random_uuid();
begin
  if not pg_has_role(session_user,'carr_writer','member') then
    raise exception 'Phase 4 Drive evidence requires the canonical runtime writer';
  end if;
  case p_evidence_kind
    when 'inventory' then expected_key:='phase4-drive-inventory'; expected_evidence:='{"inventory_complete":true,"unclassified":0}'::jsonb;
    when 'repoint' then expected_key:='phase4-drive-repoint'; expected_evidence:='{"readers_repointed":true,"writers_repointed":true}'::jsonb;
    when 'recovery' then expected_key:='phase4-drive-recovery'; expected_evidence:='{"recovery_verified":true}'::jsonb;
    when 'cutover' then expected_key:='phase4-drive-cutover'; expected_evidence:='{"legacy_drive_disabled":true}'::jsonb;
    else raise exception 'Phase 4 Drive evidence kind is not typed';
  end case;
  perform ops.phase4_lock_idempotency('drive-evidence',p_idempotency_key);
  select * into existing from ops.phase4_drive_evidence_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.source_job_receipt_id is distinct from p_job_receipt_id
       or existing.evidence_kind is distinct from p_evidence_kind then
      raise exception 'Phase 4 Drive evidence idempotency mismatch';
    end if; return existing.id;
  end if;
  select * into receipt_row from ops.job_receipt where id=p_job_receipt_id for share;
  if not found then raise exception 'Phase 4 Drive evidence lacks its governed completion receipt'; end if;
  select * into job_row from ops.job where id=receipt_row.job_id for share;
  if job_row.id is null or receipt_row.kind<>'completion' or job_row.state<>'succeeded'
     or job_row.definition_key<>expected_key or receipt_row.evidence<>expected_evidence
     or job_row.definition_key like '%scheduler%' then
    raise exception 'Phase 4 Drive evidence does not match its typed non-scheduler completion contract';
  end if;
  insert into ops.phase4_drive_evidence_receipt
    (id,receipt_ref,evidence_kind,tenant_id,source_job_receipt_id,evidence_sha256,idempotency_key)
  values(result_id,'phase4-drive-evidence:'||result_id,p_evidence_kind,'carr-internal',p_job_receipt_id,
         ops.phase4_digest(jsonb_build_object('job',to_jsonb(job_row),'receipt',to_jsonb(receipt_row))),p_idempotency_key);
  return result_id;
end $$;

create or replace function ops.record_phase4_drive_inventory(p_job_receipt_id uuid,p_idempotency_key text)
returns uuid language sql security definer set search_path=ops,public,pg_temp as $$
  select ops.phase4_insert_drive_evidence(p_job_receipt_id,'inventory',p_idempotency_key)
$$;
create or replace function ops.record_phase4_drive_repoint(p_job_receipt_id uuid,p_idempotency_key text)
returns uuid language sql security definer set search_path=ops,public,pg_temp as $$
  select ops.phase4_insert_drive_evidence(p_job_receipt_id,'repoint',p_idempotency_key)
$$;
create or replace function ops.record_phase4_drive_recovery(p_job_receipt_id uuid,p_idempotency_key text)
returns uuid language sql security definer set search_path=ops,public,pg_temp as $$
  select ops.phase4_insert_drive_evidence(p_job_receipt_id,'recovery',p_idempotency_key)
$$;
create or replace function ops.record_phase4_drive_cutover(p_job_receipt_id uuid,p_idempotency_key text)
returns uuid language sql security definer set search_path=ops,public,pg_temp as $$
  select ops.phase4_insert_drive_evidence(p_job_receipt_id,'cutover',p_idempotency_key)
$$;

create or replace function ops.approve_phase4_drive_retirement(
  p_inventory_receipt_id uuid,p_repoint_receipt_id uuid,p_recovery_receipt_id uuid,
  p_cutover_receipt_id uuid,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare existing ops.phase4_drive_retirement_authority_receipt%rowtype; result_id uuid:=gen_random_uuid(); matches integer;
begin
  if ops.authority_actor_slug()<>'joe' then raise exception 'Phase 4 whole-Drive retirement requires Joe authority'; end if;
  perform ops.phase4_lock_idempotency('drive-retirement',p_idempotency_key);
  select * into existing from ops.phase4_drive_retirement_authority_receipt where idempotency_key=p_idempotency_key;
  if found then
    if row(existing.inventory_receipt_id,existing.repoint_receipt_id,existing.recovery_receipt_id,existing.cutover_receipt_id)
       is distinct from row(p_inventory_receipt_id,p_repoint_receipt_id,p_recovery_receipt_id,p_cutover_receipt_id) then
      raise exception 'Phase 4 Drive retirement idempotency mismatch';
    end if; return existing.id;
  end if;
  select count(*) into matches from ops.phase4_drive_evidence_receipt
   where (id,evidence_kind) in ((p_inventory_receipt_id,'inventory'),(p_repoint_receipt_id,'repoint'),
                                (p_recovery_receipt_id,'recovery'),(p_cutover_receipt_id,'cutover'))
     and tenant_id='carr-internal';
  if matches<>4 then raise exception 'Phase 4 Drive retirement lacks the exact four typed immutable receipts'; end if;
  insert into ops.phase4_drive_retirement_authority_receipt
    (id,receipt_ref,tenant_id,approved_by,inventory_receipt_id,repoint_receipt_id,
     recovery_receipt_id,cutover_receipt_id,idempotency_key)
  values(result_id,'phase4-drive-retirement:'||result_id,'carr-internal','joe',p_inventory_receipt_id,
         p_repoint_receipt_id,p_recovery_receipt_id,p_cutover_receipt_id,p_idempotency_key);
  return result_id;
end $$;

-- Both projections derive tenant from the authenticated fixed reader/jobs
-- principal before their tenant predicate is evaluated.  There is no caller
-- tenant selector and neither role has direct table SELECT.
create or replace function ops.phase4_receipt_rows()
returns table(source_receipt_id uuid,stream text,source_actor_slug text,receiver_actor_slug text,
              source_session_id uuid,receiver_session_id uuid,device_id text,minted_at timestamptz,received_at timestamptz)
language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare tenant text;
begin
  select tenant_id into tenant from ops.phase4_read_principal where login_role=session_user;
  if tenant is null then raise exception 'Phase 4 receipt read requires a fixed tenant-bound reader/jobs principal'; end if;
  return query select s.id,s.stream,s.actor_slug,r.receiver_actor_slug,s.source_session_id,
                      r.receiver_session_id,r.device_id,s.minted_at,r.received_at
    from ops.phase4_source_receipt s join ops.phase4_receiver_receipt r on r.source_receipt_id=s.id
   where s.tenant_id=tenant and r.tenant_id=tenant;
end $$;

create or replace function ops.phase4_drive_retirement_rows()
returns table(receipt_id uuid,approved_by text,approved_at timestamptz)
language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare tenant text;
begin
  select tenant_id into tenant from ops.phase4_read_principal where login_role=session_user;
  if tenant is null then raise exception 'Phase 4 Drive read requires a fixed tenant-bound reader/jobs principal'; end if;
  return query select r.id,r.approved_by,r.approved_at
    from ops.phase4_drive_retirement_authority_receipt r where r.tenant_id=tenant;
end $$;

revoke all on ops.phase4_tenant_contract,ops.phase4_system_authority_contract,
  ops.phase4_actor_tenant,ops.phase4_device_partner_binding,
  ops.phase4_read_principal,ops.phase4_runtime_session,ops.phase4_source_receipt,
  ops.phase4_standing_context_source,ops.phase4_governed_retrieval_source,
  ops.phase4_tentative_write_source,ops.phase4_conflict_source,ops.phase4_privacy_scan_source,
  ops.phase4_document_download_source,ops.phase4_receiver_receipt,
  ops.phase4_drive_evidence_receipt,ops.phase4_drive_retirement_authority_receipt from public;
revoke all on function ops.phase4_digest(jsonb),ops.phase4_lock_idempotency(text,text),ops.phase4_server_session(text),
  ops.phase4_insert_source(text,text,jsonb,text),ops.phase4_insert_drive_evidence(uuid,text,text) from public;
revoke all on function ops.record_phase4_standing_context(uuid,text),
  ops.record_phase4_governed_retrieval(uuid,uuid,text),
  ops.record_phase4_tentative_write_readback(text,uuid,text),
  ops.record_phase4_conflict_undo(uuid,uuid,uuid,uuid,text),
  ops.record_phase4_privacy_scan(uuid,text),ops.record_phase4_document_download(uuid,uuid,text)
  from public,carr_reader,carr_jobs,carr_device_evidence,carr_authority;
revoke all on function ops.receive_phase4_source_receipt(uuid,text)
  from public,carr_reader,carr_jobs,carr_writer,carr_authority;
revoke all on function ops.record_phase4_drive_inventory(uuid,text),ops.record_phase4_drive_repoint(uuid,text),
  ops.record_phase4_drive_recovery(uuid,text),ops.record_phase4_drive_cutover(uuid,text)
  from public,carr_reader,carr_jobs,carr_device_evidence,carr_authority;
revoke all on function ops.approve_phase4_drive_retirement(uuid,uuid,uuid,uuid,text)
  from public,carr_reader,carr_jobs,carr_writer,carr_device_evidence;
revoke all on function ops.phase4_receipt_rows(),ops.phase4_drive_retirement_rows()
  from public,carr_writer,carr_device_evidence,carr_authority;

grant usage on schema ops to carr_reader,carr_jobs,carr_writer,carr_device_evidence,carr_authority;
grant execute on function ops.record_phase4_standing_context(uuid,text),
  ops.record_phase4_governed_retrieval(uuid,uuid,text),
  ops.record_phase4_tentative_write_readback(text,uuid,text),
  ops.record_phase4_conflict_undo(uuid,uuid,uuid,uuid,text),
  ops.record_phase4_privacy_scan(uuid,text),ops.record_phase4_document_download(uuid,uuid,text),
  ops.record_phase4_drive_inventory(uuid,text),ops.record_phase4_drive_repoint(uuid,text),
  ops.record_phase4_drive_recovery(uuid,text),ops.record_phase4_drive_cutover(uuid,text)
  to carr_writer;
grant execute on function ops.receive_phase4_source_receipt(uuid,text) to carr_device_evidence;
grant execute on function ops.approve_phase4_drive_retirement(uuid,uuid,uuid,uuid,text) to carr_authority;
grant execute on function ops.phase4_receipt_rows(),ops.phase4_drive_retirement_rows() to carr_reader,carr_jobs;

do $$ begin
  if has_table_privilege('carr_reader','ops.phase4_source_receipt','select')
     or has_table_privilege('carr_jobs','ops.phase4_source_receipt','select')
     or has_table_privilege('carr_device_evidence','ops.phase4_source_receipt','insert')
     or has_function_privilege('carr_device_evidence','ops.record_phase4_standing_context(uuid,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.receive_phase4_source_receipt(uuid,text)'::regprocedure,'execute')
     or has_function_privilege('carr_jobs','ops.approve_phase4_drive_retirement(uuid,uuid,uuid,uuid,text)'::regprocedure,'execute') then
    raise exception '0197 FAILED: Phase 4 least-privilege receipt boundary widened';
  end if;
  if not exists (
       select 1 from ops.phase4_system_authority_contract
        where contract_key='phase4_optional_continuity_v1'
          and sole_required_system_authority='joe'
          and dell_participation='optional_nonblocking'
          and not continuity_may_gate_system_rollout
          and not continuity_may_gate_system_activation
     ) then
    raise exception '0197 FAILED: Phase 4 continuity became a required system authority rail';
  end if;
end $$;

commit;
