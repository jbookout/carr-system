-- 0196_partner_continuity_trusted_boundary.sql
-- Phase 4 continuity acceptance is a read-only reduction of durable evidence.
-- It never accepts a caller JSON export and never manufactures a journey.

begin;

create table ops.partner_continuity_contract (
  contract_version integer primary key check (contract_version=1),
  contract_digest text not null check (contract_digest ~ '^[0-9a-f]{64}$'),
  tenant_id text not null check (tenant_id='carr-internal'),
  canonical_domain text not null check (canonical_domain='carr.us'),
  minimum_overlap interval not null check (minimum_overlap=interval '48 hours'),
  minimum_distinct_sessions integer not null check (minimum_distinct_sessions=3),
  maximum_cadence_gap interval not null check (maximum_cadence_gap=interval '24 hours'),
  created_at timestamptz not null default now()
);

insert into ops.partner_continuity_contract
  (contract_version,contract_digest,tenant_id,canonical_domain,minimum_overlap,
   minimum_distinct_sessions,maximum_cadence_gap)
values
  (1,'e01daa45ad5bd4a52550296065a3d43f13871ed5be04832229325c12f3c25d9f',
   'carr-internal','carr.us',interval '48 hours',3,interval '24 hours');

create table ops.partner_continuity_actor_tenant (
  actor_slug text primary key check (actor_slug in ('joe','dell')),
  tenant_id text not null check (tenant_id='carr-internal'),
  canonical_domain text not null check (canonical_domain='carr.us'),
  foreign key (actor_slug) references actor(slug) on delete restrict,
  unique (actor_slug,tenant_id)
);

insert into ops.partner_continuity_actor_tenant(actor_slug,tenant_id,canonical_domain)
values ('joe','carr-internal','carr.us'),('dell','carr-internal','carr.us');

create table ops.partner_continuity_device_principal (
  login_role name primary key,
  device_id text not null unique check (btrim(device_id)<>''),
  actor_slug text not null check (actor_slug in ('joe','dell')),
  tenant_id text not null check (tenant_id='carr-internal'),
  active boolean not null default true,
  provisioned_at timestamptz not null default now(),
  foreign key (actor_slug,tenant_id) references ops.partner_continuity_actor_tenant(actor_slug,tenant_id)
    on delete restrict
);

create table ops.partner_continuity_origin (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null check (tenant_id='carr-internal'),
  actor_slug text not null check (actor_slug in ('joe','dell')),
  stream text not null check (stream in
    ('standing_context','tentative_write_readback','conflict_undo',
     'personal_canary_privacy_telemetry','document_download')),
  session_id uuid not null,
  observed_at timestamptz not null,
  governed_origin_ref text not null check (btrim(governed_origin_ref)<>''),
  proposal_ref text,
  event_ref text,
  readback_ref text,
  privacy_telemetry_ref text,
  fetched_bytes_sha256 text,
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  created_at timestamptz not null default now(),
  foreign key (actor_slug,tenant_id) references ops.partner_continuity_actor_tenant(actor_slug,tenant_id)
    on delete restrict,
  constraint continuity_stream_evidence_is_exact check (
    (stream='standing_context' and proposal_ref is null and event_ref is null
     and readback_ref is null and privacy_telemetry_ref is null and fetched_bytes_sha256 is null)
    or (stream='tentative_write_readback' and btrim(coalesce(readback_ref,''))<>''
        and proposal_ref is null and event_ref is null and privacy_telemetry_ref is null
        and fetched_bytes_sha256 is null)
    or (stream='conflict_undo' and btrim(coalesce(proposal_ref,''))<>''
        and btrim(coalesce(event_ref,''))<>'' and readback_ref is null
        and privacy_telemetry_ref is null and fetched_bytes_sha256 is null)
    or (stream='personal_canary_privacy_telemetry' and btrim(coalesce(privacy_telemetry_ref,''))<>''
        and proposal_ref is null and event_ref is null and readback_ref is null
        and fetched_bytes_sha256 is null)
    or (stream='document_download' and fetched_bytes_sha256 ~ '^[0-9a-f]{64}$'
        and proposal_ref is null and event_ref is null and readback_ref is null
        and privacy_telemetry_ref is null)
  ),
  unique(actor_slug,stream,session_id)
);

create table ops.partner_continuity_receiver_evidence (
  id uuid primary key default gen_random_uuid(),
  origin_id uuid not null references ops.partner_continuity_origin(id) on delete restrict,
  tenant_id text not null check (tenant_id='carr-internal'),
  actor_slug text not null check (actor_slug in ('joe','dell')),
  device_id text not null,
  session_id uuid not null,
  observed_at timestamptz not null,
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  created_at timestamptz not null default now(),
  foreign key (actor_slug,tenant_id) references ops.partner_continuity_actor_tenant(actor_slug,tenant_id)
    on delete restrict,
  foreign key (device_id) references ops.partner_continuity_device_principal(device_id) on delete restrict,
  unique(origin_id,device_id,session_id)
);

create table ops.partner_continuity_drive_retirement (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null check (tenant_id='carr-internal'),
  approved_by text not null check (approved_by='joe'),
  approval_ref text not null check (btrim(approval_ref)<>'' and lower(approval_ref) not like '%scheduler%'),
  readers_repointed_ref text not null check (btrim(readers_repointed_ref)<>'' and lower(readers_repointed_ref) not like '%scheduler%'),
  writers_repointed_ref text not null check (btrim(writers_repointed_ref)<>'' and lower(writers_repointed_ref) not like '%scheduler%'),
  recovery_verified_ref text not null check (btrim(recovery_verified_ref)<>'' and lower(recovery_verified_ref) not like '%scheduler%'),
  legacy_drive_disabled_at timestamptz not null,
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  created_at timestamptz not null default now()
);

create or replace function ops.refuse_partner_continuity_rewrite()
returns trigger language plpgsql as $$
begin raise exception 'partner continuity evidence is append-only'; end $$;

create trigger partner_continuity_origin_append_only before update or delete
  on ops.partner_continuity_origin for each row execute function ops.refuse_partner_continuity_rewrite();
create trigger partner_continuity_receiver_append_only before update or delete
  on ops.partner_continuity_receiver_evidence for each row execute function ops.refuse_partner_continuity_rewrite();
create trigger partner_continuity_drive_retirement_append_only before update or delete
  on ops.partner_continuity_drive_retirement for each row execute function ops.refuse_partner_continuity_rewrite();

create or replace function ops.record_partner_continuity_origin(
  p_actor_slug text,p_stream text,p_session_id uuid,p_observed_at timestamptz,
  p_governed_origin_ref text,p_proposal_ref text,p_event_ref text,p_readback_ref text,
  p_privacy_telemetry_ref text,p_fetched_bytes_sha256 text,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare existing ops.partner_continuity_origin%rowtype; result_id uuid;
begin
  -- Exact replay is deliberately before freshness or ID generation.
  select * into existing from ops.partner_continuity_origin where idempotency_key=p_idempotency_key;
  if found then
    if existing.actor_slug is distinct from p_actor_slug or existing.stream is distinct from p_stream
       or existing.session_id is distinct from p_session_id or existing.observed_at is distinct from p_observed_at
       or existing.governed_origin_ref is distinct from p_governed_origin_ref
       or existing.proposal_ref is distinct from p_proposal_ref or existing.event_ref is distinct from p_event_ref
       or existing.readback_ref is distinct from p_readback_ref
       or existing.privacy_telemetry_ref is distinct from p_privacy_telemetry_ref
       or existing.fetched_bytes_sha256 is distinct from p_fetched_bytes_sha256 then
      raise exception 'continuity origin idempotency key was reused with different evidence';
    end if;
    return existing.id;
  end if;
  if not pg_has_role(session_user,'carr_writer','member') then raise exception 'continuity origins require carr_writer membership'; end if;
  if p_stream='standing_context' and (p_observed_at < now()-interval '15 minutes' or p_observed_at > now()+interval '5 minutes') then
    raise exception 'standing-context evidence must be freshly recorded';
  end if;
  insert into ops.partner_continuity_origin
    (tenant_id,actor_slug,stream,session_id,observed_at,governed_origin_ref,proposal_ref,event_ref,
     readback_ref,privacy_telemetry_ref,fetched_bytes_sha256,idempotency_key)
  values ('carr-internal',p_actor_slug,p_stream,p_session_id,p_observed_at,p_governed_origin_ref,p_proposal_ref,p_event_ref,
     p_readback_ref,p_privacy_telemetry_ref,p_fetched_bytes_sha256,p_idempotency_key)
  returning id into result_id;
  return result_id;
end $$;

create or replace function ops.record_partner_continuity_receiver_evidence(
  p_origin_id uuid,p_session_id uuid,p_observed_at timestamptz,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare principal ops.partner_continuity_device_principal%rowtype;
        origin ops.partner_continuity_origin%rowtype;
        existing ops.partner_continuity_receiver_evidence%rowtype; result_id uuid;
begin
  select * into principal from ops.partner_continuity_device_principal
   where login_role=session_user and active;
  if not found then raise exception 'continuity receiver requires an active tenant-bound device principal'; end if;
  select * into existing from ops.partner_continuity_receiver_evidence where idempotency_key=p_idempotency_key;
  if found then
    if existing.origin_id is distinct from p_origin_id or existing.session_id is distinct from p_session_id
       or existing.observed_at is distinct from p_observed_at or existing.actor_slug is distinct from principal.actor_slug
       or existing.device_id is distinct from principal.device_id then
      raise exception 'continuity receiver idempotency key was reused with different evidence';
    end if;
    return existing.id;
  end if;
  select * into origin from ops.partner_continuity_origin where id=p_origin_id;
  if not found or origin.tenant_id<>principal.tenant_id or origin.actor_slug<>principal.actor_slug then
    raise exception 'continuity receiver must reference a pre-existing same-tenant governed origin';
  end if;
  if p_observed_at < origin.observed_at or p_observed_at > now()+interval '5 minutes' then
    raise exception 'continuity receiver observation time is invalid';
  end if;
  insert into ops.partner_continuity_receiver_evidence
    (origin_id,tenant_id,actor_slug,device_id,session_id,observed_at,idempotency_key)
  values (origin.id,principal.tenant_id,principal.actor_slug,principal.device_id,p_session_id,p_observed_at,p_idempotency_key)
  returning id into result_id;
  return result_id;
end $$;

create or replace function ops.record_partner_continuity_drive_retirement(
  p_approval_ref text,p_readers_repointed_ref text,p_writers_repointed_ref text,
  p_recovery_verified_ref text,p_legacy_drive_disabled_at timestamptz,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare existing ops.partner_continuity_drive_retirement%rowtype; result_id uuid;
begin
  select * into existing from ops.partner_continuity_drive_retirement where idempotency_key=p_idempotency_key;
  if found then
    if existing.approval_ref is distinct from p_approval_ref or existing.readers_repointed_ref is distinct from p_readers_repointed_ref
       or existing.writers_repointed_ref is distinct from p_writers_repointed_ref
       or existing.recovery_verified_ref is distinct from p_recovery_verified_ref
       or existing.legacy_drive_disabled_at is distinct from p_legacy_drive_disabled_at then
      raise exception 'Drive retirement idempotency key was reused with different evidence';
    end if;
    return existing.id;
  end if;
  if ops.authority_actor_slug() <> 'joe' then raise exception 'Drive retirement requires Joe authority'; end if;
  insert into ops.partner_continuity_drive_retirement
    (tenant_id,approved_by,approval_ref,readers_repointed_ref,writers_repointed_ref,recovery_verified_ref,
     legacy_drive_disabled_at,idempotency_key)
  values ('carr-internal','joe',p_approval_ref,p_readers_repointed_ref,p_writers_repointed_ref,
          p_recovery_verified_ref,p_legacy_drive_disabled_at,p_idempotency_key)
  returning id into result_id;
  return result_id;
end $$;

create or replace function ops.partner_continuity_evidence_window()
returns table(actor_slug text,stream text,origin_session_id uuid,origin_observed_at timestamptz,
              receiver_session_id uuid,receiver_observed_at timestamptz,
              contract_version integer,contract_digest text)
language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
begin
  if current_setting('carr.continuity_tenant',true) is distinct from 'carr-internal' then
    raise exception 'continuity reader must select its canonical tenant before rows are returned';
  end if;
  return query
    select o.actor_slug,o.stream,o.session_id,o.observed_at,r.session_id,r.observed_at,
           c.contract_version,c.contract_digest
      from ops.partner_continuity_origin o
      join ops.partner_continuity_receiver_evidence r on r.origin_id=o.id
       and r.tenant_id=o.tenant_id and r.actor_slug=o.actor_slug
      join ops.partner_continuity_actor_tenant a on a.actor_slug=o.actor_slug and a.tenant_id=o.tenant_id
      join ops.partner_continuity_contract c on c.tenant_id=o.tenant_id and c.canonical_domain=a.canonical_domain
     where o.tenant_id=current_setting('carr.continuity_tenant',true)
       and a.canonical_domain='carr.us';
end $$;

create or replace function ops.partner_continuity_drive_retirement_status()
returns text language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
begin
  if current_setting('carr.continuity_tenant',true) is distinct from 'carr-internal' then
    raise exception 'continuity reader must select its canonical tenant before rows are returned';
  end if;
  if exists (select 1 from ops.partner_continuity_drive_retirement
             where tenant_id=current_setting('carr.continuity_tenant',true)
               and approved_by='joe' and lower(approval_ref) not like '%scheduler%'
               and lower(readers_repointed_ref) not like '%scheduler%'
               and lower(writers_repointed_ref) not like '%scheduler%'
               and lower(recovery_verified_ref) not like '%scheduler%') then return 'RETIRED'; end if;
  return 'READY_FOR_JOE_APPROVAL';
end $$;

revoke all on ops.partner_continuity_contract,ops.partner_continuity_actor_tenant,
  ops.partner_continuity_device_principal,ops.partner_continuity_origin,
  ops.partner_continuity_receiver_evidence,ops.partner_continuity_drive_retirement from public;
revoke all on function ops.record_partner_continuity_origin(text,text,uuid,timestamptz,text,text,text,text,text,text,text)
  from public,carr_reader,carr_jobs;
revoke all on function ops.record_partner_continuity_receiver_evidence(uuid,uuid,timestamptz,text)
  from public,carr_writer,carr_reader,carr_jobs;
revoke all on function ops.record_partner_continuity_drive_retirement(text,text,text,text,timestamptz,text)
  from public,carr_writer,carr_reader,carr_jobs;
revoke all on function ops.partner_continuity_evidence_window() from public,carr_writer,carr_jobs;
revoke all on function ops.partner_continuity_drive_retirement_status() from public,carr_writer,carr_jobs;
grant usage on schema ops to carr_reader,carr_writer,carr_authority;
-- Spell the catalog's canonical type name so the staging role-plan parser and
-- PostgreSQL's oidvectortypes() representation are byte-for-byte identical.
grant execute on function ops.record_partner_continuity_origin(text,text,uuid,timestamp with time zone,text,text,text,text,text,text,text) to carr_writer;
grant execute on function ops.record_partner_continuity_receiver_evidence(uuid,uuid,timestamptz,text) to carr_device_evidence;
grant execute on function ops.record_partner_continuity_drive_retirement(text,text,text,text,timestamptz,text) to carr_authority;
grant execute on function ops.partner_continuity_evidence_window(),ops.partner_continuity_drive_retirement_status() to carr_reader;

do $$ begin
  if has_table_privilege('carr_reader','ops.partner_continuity_origin','select')
     or has_table_privilege('carr_reader','ops.partner_continuity_receiver_evidence','select')
     or has_function_privilege('carr_reader','ops.record_partner_continuity_origin(text,text,uuid,timestamptz,text,text,text,text,text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.record_partner_continuity_receiver_evidence(uuid,uuid,timestamptz,text)'::regprocedure,'execute') then
    raise exception '0196 FAILED: continuity reader/writer privilege boundary widened';
  end if;
end $$;

commit;
