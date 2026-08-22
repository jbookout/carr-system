-- 0248_renewal_signed_source_ingress.sql — immutable signed source provenance.
begin;

create table ops.renewal_source_snapshot (
  id uuid primary key,
  job_id uuid not null references ops.job(id) on delete restrict,
  attempt integer not null check(attempt>0),
  provider text not null check(btrim(provider)<>''),
  key_fingerprint text not null check(key_fingerprint~'^[0-9a-f]{64}$'),
  source_observed_at timestamptz not null,
  payload_sha256 text not null check(payload_sha256~'^[0-9a-f]{64}$'),
  signature_sha256 text not null check(signature_sha256~'^[0-9a-f]{64}$'),
  row_count integer not null check(row_count>=0 and row_count<=10000),
  recorded_at timestamptz not null default now(),
  unique(job_id,attempt), unique(provider,key_fingerprint,payload_sha256)
);
create trigger renewal_source_snapshot_append_only before update or delete on ops.renewal_source_snapshot
for each row execute function ops.refuse_job_evidence_rewrite();

create table ops.renewal_source_snapshot_member (
  snapshot_id uuid not null references ops.renewal_source_snapshot(id) on delete restrict,
  source_key text not null check(btrim(source_key)<>''),
  candidate_id uuid not null references candidate_pool(id) on delete restrict,
  primary key(snapshot_id,source_key), unique(snapshot_id,candidate_id)
);
create trigger renewal_source_snapshot_member_append_only before update or delete on ops.renewal_source_snapshot_member
for each row execute function ops.refuse_job_evidence_rewrite();

alter table ops.renewal_decision_source_run add column source_snapshot_id uuid references ops.renewal_source_snapshot(id) on delete restrict;
alter table ops.renewal_decision_source_run add constraint renewal_source_run_snapshot_unique unique(source_snapshot_id);

-- This is a capability bundle, not an identity. The sole LOGIN principal is
-- provisioned out of band as carr_renewal_source_attestor and receives this
-- membership. Exact session identity checks below make a general jobs
-- credential fail closed even if it acquires unrelated grants later.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='carr_renewal_source_attestors') then
    create role carr_renewal_source_attestors nologin;
  end if;
end $$;
grant usage on schema ops,public to carr_renewal_source_attestors;

-- v1 could seal every historical mutable renewal row.  Only snapshot members
-- may now form a source run; cache extras are never evidence of completion.
revoke execute on function ops.seal_renewal_decision_source_run(uuid,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_exporter;

create or replace function ops.seal_renewal_decision_source_run(p_job_id uuid,p_lease uuid,p_snapshot_id uuid)
returns ops.renewal_decision_source_run language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_job ops.job%rowtype; v_snapshot ops.renewal_source_snapshot%rowtype; v_run ops.renewal_decision_source_run%rowtype; v_count integer;
begin
 if session_user<>'carr_renewal_source_attestor' or not pg_has_role(session_user,'carr_renewal_source_attestors','member') then raise exception using errcode='42501',message='renewal source-run sealing requires the exact renewal source attestor capability'; end if;
 select * into v_job from ops.job where id=p_job_id for update;
 if not found or v_job.definition_key<>'renewal-radar-source-daily' or v_job.definition_version<>1 or v_job.mode<>'live' or v_job.state<>'running' or v_job.lease_token is distinct from p_lease or v_job.leased_until is null or v_job.leased_until<now() then raise exception using errcode='55000',message='renewal source-run sealing requires its current static job lease'; end if;
 if v_job.scheduled_for < now()-interval '36 hours' or v_job.scheduled_for > now()+interval '5 minutes' then raise exception using errcode='22023',message='renewal source-run sealing refuses a job outside its DB-clock window'; end if;
 select * into v_snapshot from ops.renewal_source_snapshot where id=p_snapshot_id and job_id=v_job.id and attempt=v_job.attempt for update;
 if not found then raise exception using errcode='22023',message='renewal source-run sealing requires the exact leased signed source snapshot'; end if;
 if v_snapshot.row_count<>(select count(*) from ops.renewal_source_snapshot_member where snapshot_id=v_snapshot.id)
    or exists(select 1 from ops.renewal_source_snapshot_member sm left join candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id and (cp.id is null or cp.source<>'renewal-radar' or cp.status<>'pool')) then
   raise exception using errcode='23505',message='renewal source snapshot members are not a current mutable projection'; end if;
 perform pg_advisory_xact_lock(hashtextextended('renewal-decision-source-run',0));
 select * into v_run from ops.renewal_decision_source_run where job_id=v_job.id and attempt=v_job.attempt;
 if found then
   if v_run.source_snapshot_id is distinct from v_snapshot.id or v_run.member_count<>v_snapshot.row_count
      or exists((select sm.candidate_id,ops.renewal_decision_candidate_digest(cp) from ops.renewal_source_snapshot_member sm join candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id)
                except (select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id))
      or exists((select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id)
                except (select sm.candidate_id,ops.renewal_decision_candidate_digest(cp) from ops.renewal_source_snapshot_member sm join candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id)) then
     raise exception using errcode='23505',message='renewal source-run replay conflicts with immutable signed source membership';
   end if;
   return v_run;
 end if;
 select count(*) into v_count from ops.renewal_source_snapshot_member where snapshot_id=v_snapshot.id;
 insert into ops.renewal_decision_source_run(job_id,attempt,snapshot_at,member_count,source_snapshot_id) values(v_job.id,v_job.attempt,v_job.scheduled_for,v_count,v_snapshot.id) returning * into v_run;
 insert into ops.renewal_decision_source_run_member(source_run_id,candidate_id,row_digest)
 select v_run.id,sm.candidate_id,ops.renewal_decision_candidate_digest(cp) from ops.renewal_source_snapshot_member sm join candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id;
 return v_run;
end $$;

-- The jobs identity can call exactly this capability; it cannot read or write
-- candidate_pool or the raw snapshot tables.  Signature verification is done
-- before this boundary by the public-key-only adapter; this function binds that
-- verified metadata, a current lease, and a bounded, shape-checked projection.
create function ops.ingest_renewal_signed_snapshot(
  p_job_id uuid,p_lease uuid,p_snapshot_id uuid,p_provider text,p_key_fingerprint text,
  p_source_observed_at timestamptz,p_payload_sha256 text,p_signature_sha256 text,p_rows jsonb
) returns table(source_run_id uuid,row_count integer)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_job ops.job%rowtype; v_snapshot ops.renewal_source_snapshot%rowtype; v_row jsonb; v_candidate_id uuid; v_run ops.renewal_decision_source_run%rowtype; v_count integer; v_seq integer:=0; v_key text;
begin
 if session_user<>'carr_renewal_source_attestor' or not pg_has_role(session_user,'carr_renewal_source_attestors','member') then raise exception using errcode='42501',message='renewal signed ingress requires the exact renewal source attestor capability'; end if;
 if p_provider is null or btrim(p_provider)='' or octet_length(p_provider)>256 or p_key_fingerprint !~ '^[0-9a-f]{64}$' or p_payload_sha256 !~ '^[0-9a-f]{64}$' or p_signature_sha256 !~ '^[0-9a-f]{64}$' then raise exception using errcode='22023',message='renewal signed ingress provenance is malformed'; end if;
 if jsonb_typeof(p_rows)<>'array' or jsonb_array_length(p_rows)>10000 or octet_length(p_rows::text)>8388608 then raise exception using errcode='22023',message='renewal signed ingress source rows are not bounded'; end if;
 if exists(select 1 from jsonb_array_elements(p_rows) x(value) where jsonb_typeof(value)<>'object'
           or (select array_agg(key order by key) from jsonb_object_keys(value) key) is distinct from array['address','city','county','email','est_basis','est_lease_event','name','org_name','phone','segment','source_key','source_row','state','vertical']
           or nullif(btrim(value->>'source_key'),'') is null or nullif(btrim(value->>'name'),'') is null or octet_length(value->>'source_key')>512 or octet_length(value->>'name')>512 or octet_length((value->'source_row')::text)>65536 or octet_length(value::text)>131072 or jsonb_typeof(value->'source_row')<>'object') then
   raise exception using errcode='22023',message='renewal signed ingress source row shape is malformed'; end if;
 if exists(select 1 from jsonb_array_elements(p_rows) x(value) group by value->>'source_key' having count(*)<>1) then raise exception using errcode='23505',message='renewal signed ingress source key is duplicated'; end if;
 select * into v_job from ops.job where id=p_job_id for update;
 if not found or v_job.definition_key<>'renewal-radar-source-daily' or v_job.definition_version<>1 or v_job.mode<>'live' or v_job.state<>'running' or v_job.lease_token is distinct from p_lease or v_job.leased_until is null or v_job.leased_until<now() then raise exception using errcode='55000',message='renewal signed ingress requires its exact current live job lease'; end if;
 if p_source_observed_at < now()-interval '36 hours' or p_source_observed_at > now()+interval '5 minutes' then raise exception using errcode='22023',message='renewal signed ingress source observation is outside its DB-clock window'; end if;
 insert into ops.renewal_source_snapshot(id,job_id,attempt,provider,key_fingerprint,source_observed_at,payload_sha256,signature_sha256,row_count)
 values(p_snapshot_id,v_job.id,v_job.attempt,p_provider,p_key_fingerprint,p_source_observed_at,p_payload_sha256,p_signature_sha256,jsonb_array_length(p_rows)) on conflict(id) do nothing;
 select * into v_snapshot from ops.renewal_source_snapshot where id=p_snapshot_id for update;
 if not found or v_snapshot.job_id<>v_job.id or v_snapshot.attempt<>v_job.attempt or v_snapshot.provider<>p_provider or v_snapshot.key_fingerprint<>p_key_fingerprint or v_snapshot.source_observed_at<>p_source_observed_at or v_snapshot.payload_sha256<>p_payload_sha256 or v_snapshot.signature_sha256<>p_signature_sha256 or v_snapshot.row_count<>jsonb_array_length(p_rows) then raise exception using errcode='23505',message='renewal signed ingress snapshot identity conflicts with immutable receipt'; end if;
 select count(*) into v_count from ops.renewal_source_snapshot_member where snapshot_id=p_snapshot_id;
 if v_count>0 then
   if v_count<>jsonb_array_length(p_rows) or exists((select value->>'source_key' from jsonb_array_elements(p_rows) x(value)) except (select source_key from ops.renewal_source_snapshot_member where snapshot_id=p_snapshot_id)) or exists((select source_key from ops.renewal_source_snapshot_member where snapshot_id=p_snapshot_id) except (select value->>'source_key' from jsonb_array_elements(p_rows) x(value))) then raise exception using errcode='23505',message='renewal signed ingress replay conflicts with immutable source membership'; end if;
 else
   for v_row in select value from jsonb_array_elements(p_rows) x(value) loop
     v_seq:=v_seq+1; v_key:=btrim(v_row->>'source_key');
     insert into ingest_inbox(source,external_id,payload,status,triage_note) values('renewal-radar-signed',p_snapshot_id::text||':'||v_key,jsonb_build_object('provider',p_provider,'snapshot_id',p_snapshot_id,'observed_at',p_source_observed_at,'row',v_row),'new','signed renewal source ingress; payload is untrusted source data') on conflict(source,external_id) do nothing;
     insert into candidate_pool(source,source_key,source_seq,source_row,name,org_name,vertical,address,city,county,state,email,phone,segment,score,score_basis,est_lease_event,est_basis,status,created_by,updated_by)
       select 'renewal-radar',v_key,v_seq,v_row->'source_row',btrim(v_row->>'name'),nullif(btrim(v_row->>'org_name'),''),nullif(btrim(v_row->>'vertical'),''),nullif(btrim(v_row->>'address'),''),nullif(btrim(v_row->>'city'),''),nullif(btrim(v_row->>'county'),''),nullif(btrim(v_row->>'state'),''),nullif(btrim(v_row->>'email'),''),nullif(btrim(v_row->>'phone'),''),nullif(btrim(v_row->>'segment'),''),null,'unscored signed renewal source snapshot',nullif(v_row->>'est_lease_event','')::date,nullif(btrim(v_row->>'est_basis'),''),'pool',a.id,a.id from actor a where a.slug='system'
       on conflict(source,source_key) do update set source_seq=excluded.source_seq,source_row=excluded.source_row,name=excluded.name,org_name=excluded.org_name,vertical=excluded.vertical,address=excluded.address,city=excluded.city,county=excluded.county,state=excluded.state,email=excluded.email,phone=excluded.phone,segment=excluded.segment,score=null,score_basis='unscored signed renewal source snapshot',est_lease_event=excluded.est_lease_event,est_basis=excluded.est_basis,updated_by=excluded.updated_by where candidate_pool.status='pool' returning id into v_candidate_id;
     if v_candidate_id is null then raise exception using errcode='23505',message='renewal signed ingress source key conflicts with a non-pool candidate'; end if;
     insert into ops.renewal_source_snapshot_member(snapshot_id,source_key,candidate_id) values(p_snapshot_id,v_key,v_candidate_id);
     update ingest_inbox set status='filed',filed_refs=jsonb_build_object('candidate_pool',v_candidate_id::text) where source='renewal-radar-signed' and external_id=p_snapshot_id::text||':'||v_key and status='new';
   end loop;
 end if;
 select * into v_run from ops.seal_renewal_decision_source_run(p_job_id,p_lease,p_snapshot_id);
 source_run_id:=v_run.id; row_count:=v_snapshot.row_count; return next;
end $$;

-- A sealed source run invalidates only when one of its own signed members no
-- longer reconciles.  Historical cache extras are intentionally irrelevant.
create or replace view v_renewal_decision_queue_status as
with current_run as (select * from ops.renewal_decision_source_run where source_snapshot_id is not null order by snapshot_at desc,recorded_at desc,id desc limit 1),
current_members as (select r.id as source_run_id,r.recorded_at,r.member_count,m.candidate_id,cp.id is not null and cp.source='renewal-radar' and cp.status='pool' and m.row_digest=ops.renewal_decision_candidate_digest(cp) as is_current,upper(coalesce(cp.source_row->>'tier','')) like 'T1%' as is_t1 from current_run r left join ops.renewal_decision_source_run_member m on m.source_run_id=r.id left join candidate_pool cp on cp.id=m.candidate_id),
aggregate as (select max(recorded_at) as source_observed_at,coalesce(max(member_count),0) as sealed_member_count,count(*) filter(where candidate_id is not null and is_current)::integer as current_member_count,count(*) filter(where candidate_id is not null and is_current and is_t1)::integer as t1_candidate_count from current_members)
select t1_candidate_count,source_observed_at,case when source_observed_at is null or source_observed_at < now()-interval '36 hours' or sealed_member_count<>current_member_count then 'unavailable' when t1_candidate_count=0 then 'empty' else 'ready' end as freshness_state from aggregate;

-- The delivery surface must read the same signed-only run as its status
-- companion. Otherwise a later legacy unsigned run could be rendered while
-- the status view happens to describe an older signed run.
create or replace view v_renewal_decision_queue as
with current_run as (
  select * from ops.renewal_decision_source_run
   where source_snapshot_id is not null
   order by snapshot_at desc,recorded_at desc,id desc limit 1
), current_rows as (
  select cp.name as display_name,cp.org_name,cp.vertical,cp.city,cp.county,cp.state,cp.est_lease_event,
         case when upper(coalesce(cp.source_row->>'tier','')) like 'T1%' then 't1' else 'not_t1' end as tier_status,
         case when lower(coalesce(cp.source_row->>'flag','')) like 'already%' then 'already_known'
              when lower(coalesce(cp.source_row->>'flag','')) like '%not yet tenant-identified%' then 'building_signal'
              when nullif(btrim(coalesce(cp.source_row->>'flag','')),'') is null then 'clear'
              else 'review_required' end as flag_status,
         ((cp.email is not null and cp.email<>'') or (cp.phone is not null and cp.phone<>'')) as has_channel,
         r.recorded_at as source_observed_at
    from current_run r
    join ops.renewal_decision_source_run_member m on m.source_run_id=r.id
    join candidate_pool cp on cp.id=m.candidate_id and cp.source='renewal-radar' and cp.status='pool'
       and m.row_digest=ops.renewal_decision_candidate_digest(cp)
)
select display_name,org_name,vertical,city,county,state,est_lease_event,tier_status,flag_status,has_channel,
       count(*) over ()::integer as decision_count,source_observed_at,'ready'::text as freshness_state
  from current_rows
 where tier_status='t1'
   and (select freshness_state from v_renewal_decision_queue_status)='ready';

revoke all on ops.renewal_source_snapshot,ops.renewal_source_snapshot_member from public,carr_reader,carr_writer,carr_jobs,carr_exporter;
revoke all on function ops.seal_renewal_decision_source_run(uuid,uuid,uuid),ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb) from public,carr_reader,carr_writer,carr_jobs,carr_exporter;
grant execute on function ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb) to carr_renewal_source_attestors;
grant execute on function ops.seal_renewal_decision_source_run(uuid,uuid,uuid) to carr_renewal_source_attestors;
commit;
