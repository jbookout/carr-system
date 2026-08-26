-- 0307: keep Joe's local 06:30 scheduling instant as a timestamp until its
-- single, explicit America/Chicago -> UTC conversion.  0250 declared `due`
-- as timestamptz too early; PostgreSQL then interpreted 06:30 in the session
-- zone and the second conversion produced 01:30Z in a UTC session.

begin;

create or replace function ops.calendar_prebrief_joe_live_due_at(p_now timestamptz)
returns timestamptz
language plpgsql
stable
set search_path=ops,public,pg_temp
as $$
declare local_now timestamp; local_due timestamp; due timestamptz;
begin
  if p_now is null then
    raise exception 'Joe calendar scheduler requires an observed instant';
  end if;
  local_now := p_now at time zone 'America/Chicago';
  if extract(isodow from local_now) not between 1 and 5
     or local_now::time < time '06:30'
     or local_now::time >= time '06:45' then
    return null;
  end if;
  local_due := local_now::date + time '06:30';
  due := local_due at time zone 'America/Chicago';
  return due;
end $$;
revoke all on function ops.calendar_prebrief_joe_live_due_at(timestamptz) from public;
grant execute on function ops.calendar_prebrief_joe_live_due_at(timestamptz) to carr_jobs;

create or replace function ops.schedule_calendar_prebrief_joe_live_job()
returns table(job_id uuid,scheduled_for timestamptz)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare due timestamptz; jid uuid;
begin
 if session_user<>'carr_jobs' then raise exception 'Joe scheduler identity refused'; end if;
 due := ops.calendar_prebrief_joe_live_due_at(now());
 if due is null then return; end if;
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
 select (ops.enqueue_job('calendar-prebrief-projection-joe-daily',1,due,jsonb_build_object('workflow_key','calendar-prebrief-projection-joe-daily','scheduled_for',due),'calendar-prebrief:joe:'||due::text,'live')).id into jid;
 return query select jid,due;
end $$;
revoke all on function ops.schedule_calendar_prebrief_joe_live_job() from public;
grant execute on function ops.schedule_calendar_prebrief_joe_live_job() to carr_jobs;

commit;
