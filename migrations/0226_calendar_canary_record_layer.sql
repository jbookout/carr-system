-- 0226_calendar_canary_record_layer.sql
begin;
create table ops.calendar_canary_source_snapshot(
 id uuid primary key default gen_random_uuid(), job_id uuid not null references ops.job(id), attempt integer not null,
 workflow_version integer not null check(workflow_version=5), snapshot jsonb not null, snapshot_digest text not null check(snapshot_digest~'^[0-9a-f]{64}$'),
 contact_count integer not null check(contact_count>=0), created_at timestamptz not null default now(), unique(job_id,attempt),unique(id,snapshot_digest,contact_count));
create table ops.calendar_canary_receipt(
 id uuid primary key default gen_random_uuid(), job_id uuid not null references ops.job(id), attempt integer not null,
 source_snapshot_id uuid not null references ops.calendar_canary_source_snapshot(id), source_snapshot_digest text not null,
 source_contact_count integer not null, output_digest text not null check(output_digest~'^[0-9a-f]{64}$'),
 exact_count integer not null check(exact_count>=0),domain_count integer not null check(domain_count>=0),unknown_count integer not null check(unknown_count>=0),
 created_at timestamptz not null default now(),unique(job_id,attempt),foreign key(source_snapshot_id,source_snapshot_digest,source_contact_count) references ops.calendar_canary_source_snapshot(id,snapshot_digest,contact_count));
create trigger calendar_canary_source_snapshot_append_only before update or delete on ops.calendar_canary_source_snapshot for each row execute function ops.refuse_job_evidence_rewrite();
create trigger calendar_canary_receipt_append_only before update or delete on ops.calendar_canary_receipt for each row execute function ops.refuse_job_evidence_rewrite();
create or replace function ops.calendar_canary_live_job(p_job_id uuid,p_lease uuid) returns ops.job language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype; k text;
begin
 if session_user<>'carr_jobs' then raise exception using errcode='42501',message='calendar canary requires carr_jobs identity'; end if;
 select * into j from ops.job where id=p_job_id for update;
 select execution_kind into k from ops.job_definition where key=j.definition_key and version=j.definition_version;
 if not found or j.state<>'running' or j.lease_token<>p_lease or j.leased_until<now() then raise exception using errcode='55000',message='calendar canary requires current live job lease'; end if;
 if j.definition_key<>'calendar-fetch-daily' or j.definition_version<>5 or j.mode<>'canary' or k<>'deterministic' then raise exception using errcode='22023',message='job is not calendar-fetch-daily v5 deterministic canary'; end if;
 return j;
end $$;
create or replace function ops.create_calendar_canary_source_snapshot(p_job_id uuid,p_lease uuid)
returns table(id uuid,snapshot_digest text,contact_count integer,snapshot_text text) language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype;s jsonb;r ops.calendar_canary_source_snapshot%rowtype;
begin
 j:=ops.calendar_canary_live_job(p_job_id,p_lease);
 select coalesce(jsonb_agg(x order by source_rank,x->>'email',x->>'ref'),'[]') into s from(
  select jsonb_build_object('email',lower(btrim("Email")),'ref',"Client ID",'name',"Name",'org',"Practice / Entity") x,1 source_rank from v_export_clients where "Email" is not null and position('@' in btrim("Email"))>1
  union all select jsonb_build_object('email',lower(btrim("Email")),'ref',"Lead ID",'name',"Contact Name",'org',"Practice"),2 from v_export_leads where "Email" is not null and position('@' in btrim("Email"))>1)q;
 if jsonb_array_length(s)=0 then raise exception using errcode='22023',message='calendar canary source snapshot is empty'; end if;
 insert into ops.calendar_canary_source_snapshot(job_id,attempt,workflow_version,snapshot,snapshot_digest,contact_count)
 values(j.id,j.attempt,5,s,encode(digest(convert_to(s::text,'UTF8'),'sha256'),'hex'),jsonb_array_length(s)) on conflict(job_id,attempt) do nothing returning * into r;
 if not found then
  select * into r from ops.calendar_canary_source_snapshot where job_id=j.id and attempt=j.attempt;
  if r.snapshot_digest<>encode(digest(convert_to(s::text,'UTF8'),'sha256'),'hex') or r.contact_count<>jsonb_array_length(s) then raise exception using errcode='23505',message='calendar canary source snapshot replay conflicts with canonical contacts'; end if;
 end if;
 return query select r.id,r.snapshot_digest,r.contact_count,r.snapshot::text;
end $$;
create or replace function ops.record_calendar_canary_receipt(p_job_id uuid,p_lease uuid,p_source_snapshot_id uuid,p_output_digest text,p_exact integer,p_domain integer,p_unknown integer)
returns ops.calendar_canary_receipt language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype;s ops.calendar_canary_source_snapshot%rowtype;r ops.calendar_canary_receipt%rowtype;e ops.calendar_canary_receipt%rowtype;
begin
 j:=ops.calendar_canary_live_job(p_job_id,p_lease); select * into s from ops.calendar_canary_source_snapshot where id=p_source_snapshot_id and job_id=j.id and attempt=j.attempt;
 if not found then raise exception using errcode='22023',message='source snapshot is not bound to this job attempt'; end if;
 if p_output_digest!~'^[0-9a-f]{64}$' or p_exact<0 or p_domain<0 or p_unknown<0 then raise exception using errcode='22023',message='invalid calendar canary aggregate'; end if;
 insert into ops.calendar_canary_receipt(job_id,attempt,source_snapshot_id,source_snapshot_digest,source_contact_count,output_digest,exact_count,domain_count,unknown_count)
 values(j.id,j.attempt,s.id,s.snapshot_digest,s.contact_count,p_output_digest,p_exact,p_domain,p_unknown) on conflict(job_id,attempt) do nothing returning * into r;
 if found then return r; end if; select * into e from ops.calendar_canary_receipt where job_id=j.id and attempt=j.attempt;
 if e.source_snapshot_id<>s.id or e.output_digest<>p_output_digest or e.exact_count<>p_exact or e.domain_count<>p_domain or e.unknown_count<>p_unknown then raise exception using errcode='23505',message='calendar canary receipt replay conflicts with immutable attempt'; end if; return e;
end $$;
create or replace function ops.resolve_calendar_canary_receipt(p_job_id uuid,p_attempt integer) returns setof ops.calendar_canary_receipt language sql security definer set search_path=ops,public,pg_temp as $$select * from ops.calendar_canary_receipt where job_id=p_job_id and attempt=p_attempt$$;
revoke all on ops.calendar_canary_source_snapshot,ops.calendar_canary_receipt from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.calendar_canary_live_job(uuid,uuid),ops.create_calendar_canary_source_snapshot(uuid,uuid),ops.record_calendar_canary_receipt(uuid,uuid,uuid,text,integer,integer,integer),ops.resolve_calendar_canary_receipt(uuid,integer) from public;
grant execute on function ops.create_calendar_canary_source_snapshot(uuid,uuid),ops.record_calendar_canary_receipt(uuid,uuid,uuid,text,integer,integer,integer),ops.resolve_calendar_canary_receipt(uuid,integer) to carr_jobs;
commit;
