-- 0227_nightly_availability_canary_record_layer.sql
-- The Nightly canary is deliberately one read-only availability-matcher
-- subchain.  It receives a DB-minted snapshot through the parent, never a DB
-- URL or caller-authored JSON, and records only a lease-bound aggregate.

begin;

create table ops.nightly_availability_canary_source_snapshot(
 id uuid primary key default gen_random_uuid(),
 job_id uuid not null references ops.job(id), attempt integer not null,
 workflow_version integer not null check(workflow_version=3),
 snapshot jsonb not null, snapshot_digest text not null check(snapshot_digest~'^[0-9a-f]{64}$'),
 availability_count integer not null check(availability_count>=0),
 open_search_count integer not null check(open_search_count>=0),
 created_at timestamptz not null default now(), unique(job_id,attempt),
 unique(id,snapshot_digest,availability_count,open_search_count)
);
create table ops.nightly_availability_canary_receipt(
 id uuid primary key default gen_random_uuid(),
 job_id uuid not null references ops.job(id), attempt integer not null,
 source_snapshot_id uuid not null references ops.nightly_availability_canary_source_snapshot(id),
 source_snapshot_digest text not null,
 availability_count integer not null check(availability_count>=0),
 open_search_count integer not null check(open_search_count>=0),
 match_count integer not null check(match_count>=0), output_digest text not null check(output_digest~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default now(), unique(job_id,attempt),
 foreign key(source_snapshot_id,source_snapshot_digest,availability_count,open_search_count)
   references ops.nightly_availability_canary_source_snapshot(id,snapshot_digest,availability_count,open_search_count)
);
create trigger nightly_availability_canary_source_append_only before update or delete on ops.nightly_availability_canary_source_snapshot for each row execute function ops.refuse_job_evidence_rewrite();
create trigger nightly_availability_canary_receipt_append_only before update or delete on ops.nightly_availability_canary_receipt for each row execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.nightly_availability_canary_live_job(p_job_id uuid,p_lease uuid)
returns ops.job language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype; k text;
begin
 if session_user<>'carr_jobs' then raise exception using errcode='42501',message='nightly availability canary requires carr_jobs identity'; end if;
 select * into j from ops.job where id=p_job_id for update;
 select execution_kind into k from ops.job_definition where key=j.definition_key and version=j.definition_version;
 if not found or j.state<>'running' or j.lease_token<>p_lease or j.leased_until<now() then raise exception using errcode='55000',message='nightly availability canary requires current live job lease'; end if;
 if j.definition_key<>'nightly-record-layer' or j.definition_version<>3 or j.mode<>'canary' or k<>'deterministic' then raise exception using errcode='22023',message='job is not nightly-record-layer v3 deterministic canary'; end if;
 return j;
end $$;

create or replace function ops.create_nightly_availability_canary_source_snapshot(p_job_id uuid,p_lease uuid)
returns table(id uuid,snapshot_digest text,availability_count integer,open_search_count integer,snapshot_text text)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype;s jsonb;r ops.nightly_availability_canary_source_snapshot%rowtype;
begin
 j:=ops.nightly_availability_canary_live_job(p_job_id,p_lease);
 select jsonb_build_object(
   'availabilities',coalesce((select jsonb_agg(x order by x->>'id') from (
      select jsonb_build_object('id',av.id::text,'status',av.status,'rate_norm',av.rate_norm_sf_yr,
        'owed',av.norm_owed,'available_on',av.available_on,'observed',av.observed_at::date,
        'source',av.source,'area',sp.area_amount,'suite',sp.suite,'city',b.city,'state',b.state,
        'sub_type',b.sub_type,'address',b.address,'bname',b.name) x
        from (select distinct on (av.space_id) av.* from availability av
               order by av.space_id,av.observed_at desc,av.id desc) av
        join space sp on sp.id=av.space_id join building b on b.id=sp.building_id)q),'[]'::jsonb),
   'searches',coalesce((select jsonb_agg(x order by x->>'ref',x->>'id') from (
      select jsonb_build_object('id',s.id::text,'spec',s.spec,'ref',coalesce(c.roster_ref,''),'name',p.name) x
        from space_search s join client c on c.id=s.client_id join party p on p.id=c.party_id where s.status='open')q),'[]'::jsonb)) into s;
 if jsonb_array_length(s->'availabilities')=0 or jsonb_array_length(s->'searches')=0 then
   raise exception using errcode='22023',message='nightly availability canary source must contain availability and open-search evidence';
 end if;
 insert into ops.nightly_availability_canary_source_snapshot(job_id,attempt,workflow_version,snapshot,snapshot_digest,availability_count,open_search_count)
 values(j.id,j.attempt,3,s,encode(digest(convert_to(s::text,'UTF8'),'sha256'),'hex'),jsonb_array_length(s->'availabilities'),jsonb_array_length(s->'searches'))
 on conflict(job_id,attempt) do nothing returning * into r;
 if not found then
  select * into r from ops.nightly_availability_canary_source_snapshot where job_id=j.id and attempt=j.attempt;
  if r.snapshot_digest<>encode(digest(convert_to(s::text,'UTF8'),'sha256'),'hex') or r.availability_count<>jsonb_array_length(s->'availabilities') or r.open_search_count<>jsonb_array_length(s->'searches') then raise exception using errcode='23505',message='nightly availability canary source replay conflicts with canonical snapshot'; end if;
 end if;
 return query select r.id,r.snapshot_digest,r.availability_count,r.open_search_count,r.snapshot::text;
end $$;

create or replace function ops.record_nightly_availability_canary_receipt(p_job_id uuid,p_lease uuid,p_source_snapshot_id uuid,p_output_digest text,p_match_count integer)
returns ops.nightly_availability_canary_receipt language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype;s ops.nightly_availability_canary_source_snapshot%rowtype;r ops.nightly_availability_canary_receipt%rowtype;e ops.nightly_availability_canary_receipt%rowtype;
begin
 j:=ops.nightly_availability_canary_live_job(p_job_id,p_lease);
 select * into s from ops.nightly_availability_canary_source_snapshot where id=p_source_snapshot_id and job_id=j.id and attempt=j.attempt;
 if not found then raise exception using errcode='22023',message='nightly availability source snapshot is not bound to this job attempt'; end if;
 if p_output_digest!~'^[0-9a-f]{64}$' or p_match_count<0 then raise exception using errcode='22023',message='invalid nightly availability canary aggregate'; end if;
 insert into ops.nightly_availability_canary_receipt(job_id,attempt,source_snapshot_id,source_snapshot_digest,availability_count,open_search_count,match_count,output_digest)
 values(j.id,j.attempt,s.id,s.snapshot_digest,s.availability_count,s.open_search_count,p_match_count,p_output_digest)
 on conflict(job_id,attempt) do nothing returning * into r;
 if found then return r; end if;
 select * into e from ops.nightly_availability_canary_receipt where job_id=j.id and attempt=j.attempt;
 if e.source_snapshot_id<>s.id or e.output_digest<>p_output_digest or e.match_count<>p_match_count then raise exception using errcode='23505',message='nightly availability canary receipt replay conflicts with immutable attempt'; end if;
 return e;
end $$;

create or replace function ops.resolve_nightly_availability_canary_receipt(p_job_id uuid,p_attempt integer)
returns setof ops.nightly_availability_canary_receipt language sql security definer set search_path=ops,public,pg_temp as $$
 select * from ops.nightly_availability_canary_receipt where job_id=p_job_id and attempt=p_attempt
$$;

revoke all on ops.nightly_availability_canary_source_snapshot,ops.nightly_availability_canary_receipt from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.nightly_availability_canary_live_job(uuid,uuid),ops.create_nightly_availability_canary_source_snapshot(uuid,uuid),ops.record_nightly_availability_canary_receipt(uuid,uuid,uuid,text,integer),ops.resolve_nightly_availability_canary_receipt(uuid,integer) from public;
grant execute on function ops.create_nightly_availability_canary_source_snapshot(uuid,uuid),ops.record_nightly_availability_canary_receipt(uuid,uuid,uuid,text,integer),ops.resolve_nightly_availability_canary_receipt(uuid,integer) to carr_jobs;

commit;
