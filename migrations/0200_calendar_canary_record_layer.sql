begin;

create table if not exists ops.calendar_canary_destination (
  destination_id text primary key check (destination_id ~ '^calendar-canary-[a-z0-9_-]+$'),
  database_name text not null unique check (database_name <> ''),
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists ops.calendar_canary_receipt (
  id uuid primary key default gen_random_uuid(),
  destination_id text not null references ops.calendar_canary_destination(destination_id) on delete restrict,
  idempotency_key text not null check (idempotency_key ~ '^calendar-canary-v1:[0-9a-f]{64}$'),
  source_digest text not null check (source_digest ~ '^[0-9a-f]{64}$'),
  output_digest text not null check (output_digest ~ '^[0-9a-f]{64}$'),
  exact_count integer not null check (exact_count >= 0),
  created_at timestamptz not null default now(),
  unique (destination_id,idempotency_key)
);
drop trigger if exists calendar_canary_receipt_append_only on ops.calendar_canary_receipt;
create trigger calendar_canary_receipt_append_only before update or delete on ops.calendar_canary_receipt for each row execute function ops.refuse_job_evidence_rewrite();
create or replace function ops.record_calendar_canary_receipt(p_destination_id text,p_idempotency_key text,p_source_digest text,p_output_digest text,p_exact_count integer)
returns ops.calendar_canary_receipt language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare destination ops.calendar_canary_destination%rowtype; existing ops.calendar_canary_receipt%rowtype; inserted ops.calendar_canary_receipt%rowtype;
begin
  if session_user <> 'carr_jobs' then raise exception 'calendar canary receipt requires carr_jobs identity'; end if;
  select * into destination from ops.calendar_canary_destination where destination_id=p_destination_id and active for key share;
  if not found then raise exception 'calendar canary destination % is not active',p_destination_id; end if;
  if destination.database_name <> current_database() then raise exception 'calendar canary destination % is bound to %, not current database %',p_destination_id,destination.database_name,current_database(); end if;
  insert into ops.calendar_canary_receipt(destination_id,idempotency_key,source_digest,output_digest,exact_count) values (p_destination_id,p_idempotency_key,p_source_digest,p_output_digest,p_exact_count) on conflict (destination_id,idempotency_key) do nothing returning * into inserted;
  if found then return inserted; end if;
  select * into existing from ops.calendar_canary_receipt where destination_id=p_destination_id and idempotency_key=p_idempotency_key;
  if existing.source_digest <> p_source_digest or existing.output_digest <> p_output_digest or existing.exact_count <> p_exact_count then raise exception 'calendar canary idempotency key conflicts with prior receipt'; end if;
  return existing;
end $$;
create or replace function ops.resolve_calendar_canary_receipt(p_destination_id text,p_idempotency_key text)
returns table(id uuid,destination_id text,idempotency_key text,source_digest text,output_digest text,exact_count integer,created_at timestamptz)
language sql security definer set search_path=ops,public,pg_temp as $$
  select r.id,r.destination_id,r.idempotency_key,r.source_digest,r.output_digest,r.exact_count,r.created_at
  from ops.calendar_canary_receipt r where r.destination_id=p_destination_id and r.idempotency_key=p_idempotency_key
$$;
revoke all on ops.calendar_canary_destination,ops.calendar_canary_receipt from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.record_calendar_canary_receipt(text,text,text,text,integer) from public;
revoke all on function ops.resolve_calendar_canary_receipt(text,text) from public;
grant execute on function ops.record_calendar_canary_receipt(text,text,text,text,integer) to carr_jobs;
grant execute on function ops.resolve_calendar_canary_receipt(text,text) to carr_reader,carr_jobs;
commit;
