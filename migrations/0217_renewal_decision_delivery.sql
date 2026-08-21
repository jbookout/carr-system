-- 0217_renewal_decision_delivery.sql — safe, record-native renewal delivery.
--
-- A mutable candidate_pool.updated_at is not evidence that the renewal source
-- completed.  The queue therefore reads only rows bound to the latest immutable,
-- lease-checked source run.  The database, not the caller, supplies every time,
-- count, and digest used for freshness.

begin;

insert into ops.job_definition
  (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
   inventory_contract,recurrence,state_contract,routing_contract,
   filtering_contract,validation_contract,retry_policy,deduplication,
   completion_contract,legacy_schedule)
values
  ('renewal-radar-source-daily',1,false,'yellow','system','deterministic',
   '{"entrypoint":"ops.seal_renewal_decision_source_run","activation":"pending source-run adapter"}'::jsonb,
   '{"owner":"ops.job","inputs":["renewal-radar candidate import"],"canonical_writes":["ops.renewal_decision_source_run","ops.renewal_decision_source_run_member"]}'::jsonb,
   '{"cron":null,"timezone":"America/Chicago","source":"disabled pending source-run adapter"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_complete"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.pool_imported"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_run_sealed"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"renewal-radar-source-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_run_sealed"]},"receipt_kind":"renewal_source_run"}'::jsonb,
   '{"provider":"none","status":"disabled","activation":"explicit source-run adapter required"}'::jsonb);

create table ops.renewal_decision_source_run (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id) on delete restrict,
  attempt integer not null check (attempt > 0),
  snapshot_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  member_count integer not null check (member_count >= 0),
  unique (job_id,attempt)
);
create trigger renewal_decision_source_run_append_only
  before update or delete on ops.renewal_decision_source_run
  for each row execute function ops.refuse_job_evidence_rewrite();

create table ops.renewal_decision_source_run_member (
  source_run_id uuid not null references ops.renewal_decision_source_run(id) on delete restrict,
  candidate_id uuid not null references candidate_pool(id) on delete restrict,
  row_digest text not null check (row_digest ~ '^[0-9a-f]{64}$'),
  primary key (source_run_id,candidate_id)
);
create trigger renewal_decision_source_run_member_append_only
  before update or delete on ops.renewal_decision_source_run_member
  for each row execute function ops.refuse_job_evidence_rewrite();

-- This digest is intentionally DB-computed and never exposed.  It binds every
-- source field (including the raw source payload) while excluding only touch
-- metadata.  A later row edit makes the old sealed member ineligible.
create or replace function ops.renewal_decision_candidate_digest(p_candidate candidate_pool)
returns text language sql stable security definer set search_path=ops,public,pg_temp as $$
  select encode(digest(convert_to((to_jsonb(p_candidate) - array['updated_at','updated_by'])::text,'UTF8'),'sha256'),'hex'
$$;

create or replace function ops.seal_renewal_decision_source_run(p_job_id uuid,p_lease uuid)
returns ops.renewal_decision_source_run
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  v_job ops.job%rowtype;
  v_run ops.renewal_decision_source_run%rowtype;
  v_count integer;
begin
  if not pg_has_role(session_user,'carr_jobs','member') then
    raise exception using errcode='42501',message='renewal source-run sealing requires the jobs capability';
  end if;
  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.definition_key<>'renewal-radar-source-daily' or v_job.definition_version<>1
     or v_job.mode<>'live' or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='renewal source-run sealing requires its current static job lease';
  end if;
  if v_job.scheduled_for < now()-interval '36 hours' or v_job.scheduled_for > now()+interval '5 minutes' then
    raise exception using errcode='22023',message='renewal source-run sealing refuses a job outside its DB-clock window';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('renewal-decision-source-run',0));
  select * into v_run from ops.renewal_decision_source_run where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_run.member_count <> (select count(*) from candidate_pool where source='renewal-radar' and status='pool')
       or exists (
          (select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id)
          except
          (select cp.id,ops.renewal_decision_candidate_digest(cp) from candidate_pool cp where cp.source='renewal-radar' and cp.status='pool')
       ) or exists (
          (select cp.id,ops.renewal_decision_candidate_digest(cp) from candidate_pool cp where cp.source='renewal-radar' and cp.status='pool')
          except
          (select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id)
       ) then
      raise exception using errcode='23505',message='renewal source-run replay conflicts with immutable source membership';
    end if;
    return v_run;
  end if;
  select count(*) into v_count from candidate_pool where source='renewal-radar' and status='pool';
  insert into ops.renewal_decision_source_run(job_id,attempt,snapshot_at,member_count)
  values(v_job.id,v_job.attempt,v_job.scheduled_for,v_count) returning * into v_run;
  insert into ops.renewal_decision_source_run_member(source_run_id,candidate_id,row_digest)
    select v_run.id,cp.id,ops.renewal_decision_candidate_digest(cp)
      from candidate_pool cp where cp.source='renewal-radar' and cp.status='pool';
  return v_run;
end $$;

create or replace view v_renewal_decision_queue_status as
with current_run as (
  select * from ops.renewal_decision_source_run order by recorded_at desc,id desc limit 1
), current_members as (
  select r.id as source_run_id,r.recorded_at,r.member_count,m.candidate_id,
         cp.id is not null and cp.source='renewal-radar' and cp.status='pool'
           and m.row_digest=ops.renewal_decision_candidate_digest(cp) as is_current,
         upper(coalesce(cp.source_row->>'tier','')) like 'T1%' as is_t1
    from current_run r
    left join ops.renewal_decision_source_run_member m on m.source_run_id=r.id
    left join candidate_pool cp on cp.id=m.candidate_id
), aggregate as (
  select max(recorded_at) as source_observed_at,
         coalesce(max(member_count),0) as sealed_member_count,
         count(*) filter (where candidate_id is not null and is_current)::integer as current_member_count,
         count(*) filter (where candidate_id is not null and is_current and is_t1)::integer as t1_candidate_count
    from current_members
)
select t1_candidate_count,source_observed_at,
       case when source_observed_at is null
                   or source_observed_at < now()-interval '36 hours'
                   or sealed_member_count<>current_member_count then 'unavailable'
            when t1_candidate_count=0 then 'empty'
            else 'ready' end as freshness_state
  from aggregate;

create or replace view v_renewal_decision_queue as
with current_run as (
  select * from ops.renewal_decision_source_run order by recorded_at desc,id desc limit 1
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

comment on view v_renewal_decision_queue is
  'Safe T1 renewal queue. Rows are visible only when an immutable DB-owned source run is current; never add candidate IDs, source fields, email, phone, or address.';
comment on view v_renewal_decision_queue_status is
  'One-row renewal source-run state. ready is a current sealed nonempty T1 queue; empty is a current sealed true zero; unavailable is missing, stale, or altered source membership.';

revoke all on ops.renewal_decision_source_run,ops.renewal_decision_source_run_member from public,carr_reader,carr_writer,carr_jobs,carr_exporter;
revoke all on function ops.renewal_decision_candidate_digest(candidate_pool),ops.seal_renewal_decision_source_run(uuid,uuid) from public,carr_reader,carr_writer,carr_exporter;
grant execute on function ops.seal_renewal_decision_source_run(uuid,uuid) to carr_jobs;
grant execute on function ops.renewal_decision_candidate_digest(candidate_pool) to carr_reader;
revoke all on v_renewal_decision_queue,v_renewal_decision_queue_status from public;
grant select on v_renewal_decision_queue,v_renewal_decision_queue_status to carr_reader;

do $$
declare forbidden text[] := array['id','pool_id','source_key','source_row','email','phone','address'];
begin
  if exists (select 1 from information_schema.columns where table_schema='public' and table_name='v_renewal_decision_queue' and column_name=any(forbidden)) then
    raise exception '0217 FAILED: renewal decision queue exposes a prohibited raw field';
  end if;
  if not has_table_privilege('carr_reader','v_renewal_decision_queue','select')
     or not has_table_privilege('carr_reader','v_renewal_decision_queue_status','select')
     or has_table_privilege('carr_reader','candidate_pool','select')
     or has_table_privilege('carr_reader','v_export_pool','select') then
    raise exception '0217 FAILED: renewal reader grant boundary is wrong';
  end if;
  if has_table_privilege('carr_reader','ops.renewal_decision_source_run','select')
     or has_table_privilege('carr_reader','ops.renewal_decision_source_run_member','select')
     or has_table_privilege('carr_jobs','ops.renewal_decision_source_run','select')
     or has_table_privilege('carr_jobs','ops.renewal_decision_source_run_member','select') then
    raise exception '0217 FAILED: renewal source-run base tables leaked';
  end if;
end $$;

commit;
