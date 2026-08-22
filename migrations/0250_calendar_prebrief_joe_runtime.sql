-- Dedicated Joe prebrief runtime: the generic claim surface is intentionally
-- too broad for an EventKit-capable device.
begin;

create table ops.calendar_prebrief_runtime_activation_receipt(
 id uuid primary key default gen_random_uuid(),sponsor text not null check(sponsor='joe'),
 app_evidence_digest text not null check(app_evidence_digest~'^[0-9a-f]{64}$'),
 allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
 activated_by text not null check(activated_by='joe'),activated_at timestamptz not null default now());
create trigger calendar_prebrief_runtime_activation_receipt_append_only
  before update or delete on ops.calendar_prebrief_runtime_activation_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();
create or replace function ops.activate_calendar_prebrief_joe_live(p_evidence_digest text)
returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare a ops.calendar_prebrief_allowed_calendar%rowtype; rid uuid; v_definition_count integer;
begin
 if session_user<>'carr_authority_joe' or p_evidence_digest !~ '^[0-9a-f]{64}$' then raise exception 'Joe activation authority or evidence refused'; end if;
 select * into a from ops.calendar_prebrief_allowed_calendar where sponsor='joe' for update;
 if not found or a.active_revision_id is null or not exists(select 1 from ops.calendar_prebrief_allowlist_receipt where id=a.active_revision_id and sponsor='joe' and configuration_digest=a.configuration_digest) then raise exception 'Joe activation requires current allowlist receipt'; end if;
 insert into ops.calendar_prebrief_runtime_activation_receipt(sponsor,app_evidence_digest,allowlist_revision_id,activated_by) values('joe',p_evidence_digest,a.active_revision_id,'joe') returning id into rid;
 update ops.job_definition set enabled=true,updated_at=now()
  where key='calendar-prebrief-projection-joe-daily' and version=1
    and owner_actor='joe';
 get diagnostics v_definition_count = row_count;
 if v_definition_count<>1 then
   raise exception 'Joe activation requires exactly one Joe live definition';
 end if;
 return rid;
end $$;
revoke all on ops.calendar_prebrief_runtime_activation_receipt from public;
revoke all on function ops.activate_calendar_prebrief_joe_live(text) from public;
grant execute on function ops.activate_calendar_prebrief_joe_live(text) to carr_authority;
create or replace function ops.read_calendar_prebrief_joe_activation(p_id uuid)
returns table(id uuid,sponsor text,app_evidence_digest text,allowlist_revision_id uuid,activated_at timestamptz)
language sql security definer set search_path=ops,public,pg_temp as $$ select id,sponsor,app_evidence_digest,allowlist_revision_id,activated_at from ops.calendar_prebrief_runtime_activation_receipt where id=p_id and session_user='carr_authority_joe' $$;
revoke all on function ops.read_calendar_prebrief_joe_activation(uuid) from public;
grant execute on function ops.read_calendar_prebrief_joe_activation(uuid) to carr_authority;

create or replace function ops.claim_calendar_prebrief_joe_live_job(p_worker text,p_lease_seconds integer default 300)
returns table(job_id uuid,lease uuid,scheduled_for timestamptz)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v ops.job%rowtype;
begin
 if session_user<>'carr_jobs' or btrim(coalesce(p_worker,''))='' or p_lease_seconds not between 1 and 300 then raise exception 'Joe calendar runtime identity or lease refused'; end if;
 if not exists(
   select 1
     from ops.calendar_prebrief_allowed_calendar a
     join lateral (
       select r.allowlist_revision_id
         from ops.calendar_prebrief_runtime_activation_receipt r
        where r.sponsor='joe'
        order by r.activated_at desc,r.id desc limit 1
     ) latest on latest.allowlist_revision_id=a.active_revision_id
     join ops.calendar_prebrief_allowlist_receipt l
       on l.id=a.active_revision_id and l.sponsor='joe'
      and l.configuration_digest=a.configuration_digest
    where a.sponsor='joe'
 ) then raise exception 'Joe claim activation gate refused'; end if;
 perform ops.reap_expired_jobs();
 select j.* into v from ops.job j join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version where d.enabled and j.definition_key='calendar-prebrief-projection-joe-daily' and j.definition_version=1 and d.owner_actor='joe' and j.mode='live' and j.state in ('queued','retry_wait') and j.next_attempt_at<=now() order by j.scheduled_for,j.created_at for update of j,d skip locked limit 1;
 if not found then return; end if;
 update ops.job set state='running',attempt=v.attempt+1,lease_owner=p_worker,lease_token=gen_random_uuid(),leased_until=now()+make_interval(secs=>p_lease_seconds),started_at=coalesce(started_at,now()),updated_at=now() where id=v.id returning * into v;
 insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state) values(v.id,v.attempt,v.lease_owner,v.lease_token,'running');
 return query select v.id,v.lease_token,v.scheduled_for;
end $$;
revoke all on function ops.claim_calendar_prebrief_joe_live_job(text,integer) from public;
grant execute on function ops.claim_calendar_prebrief_joe_live_job(text,integer) to carr_jobs;

-- Generic workers must never race the sponsor-bound Mac runtime for this
-- EventKit-capable job. Keep the established generic semantics for every
-- other definition while fencing this exact dedicated version in the DB.
create or replace function ops.claim_job(
  p_worker text,p_limit integer default 1,p_lease_seconds integer default 300
) returns table (
  job_id uuid,lease_token uuid,definition_key text,definition_version integer,
  payload jsonb,execution_kind text,execution_contract jsonb,
  attempt integer,timeout_seconds integer,mode text
) language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_limit<1 or p_lease_seconds<1 then
    raise exception 'worker, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id from ops.job j join ops.job_definition d
      on d.key=j.definition_key and d.version=j.definition_version
     where d.enabled and j.state in ('queued','retry_wait') and j.next_attempt_at<=now()
       and not (j.definition_key='calendar-prebrief-projection-joe-daily' and j.definition_version=1)
     order by j.scheduled_for,j.created_at for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
      from candidate c where j.id=c.id returning j.*
  ), attempts(claimed_job_id) as (
    insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning ops.job_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c join ops.job_definition d
      on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.claimed_job_id=c.id;
end $$;

create or replace function ops.claim_job_mode(
  p_worker text,p_mode text,p_limit integer default 1,p_lease_seconds integer default 300
) returns table (
  job_id uuid,lease_token uuid,definition_key text,definition_version integer,
  payload jsonb,execution_kind text,execution_contract jsonb,
  attempt integer,timeout_seconds integer,mode text
) language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_mode not in ('shadow','canary','live','replay')
     or p_limit<1 or p_lease_seconds<1 then
    raise exception 'worker, valid mode, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id from ops.job j join ops.job_definition d
      on d.key=j.definition_key and d.version=j.definition_version
     where d.enabled and j.state in ('queued','retry_wait') and j.next_attempt_at<=now()
       and j.mode=p_mode
       and not (j.definition_key='calendar-prebrief-projection-joe-daily' and j.definition_version=1)
     order by j.scheduled_for,j.created_at for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
      from candidate c where j.id=c.id returning j.*
  ), attempts(claimed_job_id) as (
    insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning ops.job_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c join ops.job_definition d
      on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.claimed_job_id=c.id;
end $$;
revoke all on function ops.claim_job(text,integer,integer),ops.claim_job_mode(text,text,integer,integer) from public;
grant execute on function ops.claim_job(text,integer,integer),ops.claim_job_mode(text,text,integer,integer) to carr_jobs;

create or replace function ops.schedule_calendar_prebrief_joe_live_job()
returns table(job_id uuid,scheduled_for timestamptz) language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare local_now timestamp; due timestamptz; jid uuid;
begin
 if session_user<>'carr_jobs' then raise exception 'Joe scheduler identity refused'; end if;
 local_now:=now() at time zone 'America/Chicago';
 if extract(isodow from local_now) not between 1 and 5 or local_now::time < time '06:30' or local_now::time >= time '06:45' then return; end if;
 if not exists(select 1 from ops.job_definition where key='calendar-prebrief-projection-joe-daily' and version=1 and owner_actor='joe' and enabled) or not exists(
   select 1
     from ops.calendar_prebrief_allowed_calendar a
     join lateral (
       select r.allowlist_revision_id
         from ops.calendar_prebrief_runtime_activation_receipt r
        where r.sponsor='joe'
        order by r.activated_at desc,r.id desc limit 1
     ) latest on latest.allowlist_revision_id=a.active_revision_id
     join ops.calendar_prebrief_allowlist_receipt l
       on l.id=a.active_revision_id and l.sponsor='joe'
      and l.configuration_digest=a.configuration_digest
    where a.sponsor='joe'
 ) then raise exception 'Joe scheduler activation gate refused'; end if;
 due:=date_trunc('day',local_now) + interval '6 hours 30 minutes'; due:=due at time zone 'America/Chicago';
 select (ops.enqueue_job('calendar-prebrief-projection-joe-daily',1,due,jsonb_build_object('workflow_key','calendar-prebrief-projection-joe-daily','scheduled_for',due),'calendar-prebrief:joe:'||due::text,'live')).id into jid;
 return query select jid,due;
end $$;
revoke all on function ops.schedule_calendar_prebrief_joe_live_job() from public;
grant execute on function ops.schedule_calendar_prebrief_joe_live_job() to carr_jobs;

create or replace function ops.complete_calendar_prebrief_joe_live_job(p_job uuid,p_lease uuid,p_attestation uuid,p_receipt uuid)
returns table(job_id uuid,attempt integer,state text,attestation_id uuid,receipt_id uuid,allowlist_revision_id uuid,allowlist_digest text,scheduled_for timestamptz)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare j ops.job%rowtype; r ops.calendar_prebrief_projection_receipt%rowtype; a ops.calendar_prebrief_source_attestation_receipt%rowtype;
begin
 if session_user<>'carr_jobs' then raise exception 'Joe completion identity refused'; end if;
 select * into j from ops.job where id=p_job for update;
 if not found or j.definition_key<>'calendar-prebrief-projection-joe-daily' or j.definition_version<>1 or j.mode<>'live' or j.state<>'running' or j.lease_token is distinct from p_lease then raise exception 'Joe completion lease refused'; end if;
 select * into r from ops.calendar_prebrief_projection_receipt where job_id=j.id and attempt=j.attempt and id=p_receipt;
 select * into a from ops.calendar_prebrief_source_attestation_receipt where job_id=j.id and attempt=j.attempt and id=p_attestation;
 if r.id is null or a.id is null or r.source_attestation_id<>a.id or r.sponsor<>'joe' or a.sponsor<>'joe' or a.lease_token<>p_lease then raise exception 'Joe completion evidence refused'; end if;
 perform ops.complete_job(j.id,p_lease,jsonb_build_object('sponsor','joe','mode','live','attestation_id',p_attestation,'receipt_id',p_receipt,'allowlist_revision_id',r.allowlist_revision_id,'allowlist_digest',r.allowlist_digest),'calendar-prebrief:joe:'||j.id::text||':'||j.attempt::text);
 return query select j.id,j.attempt,'succeeded'::text,a.id,r.id,r.allowlist_revision_id,r.allowlist_digest,j.scheduled_for;
end $$;
revoke all on function ops.complete_calendar_prebrief_joe_live_job(uuid,uuid,uuid,uuid) from public;
grant execute on function ops.complete_calendar_prebrief_joe_live_job(uuid,uuid,uuid,uuid) to carr_jobs;
commit;
