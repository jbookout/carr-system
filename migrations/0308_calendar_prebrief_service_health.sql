-- 0308_calendar_prebrief_service_health.sql
--
-- calendar-prebrief-joe is deliberately not wrapped by run-scheduled.sh: its
-- narrow carr_jobs runtime already writes an exact ops.job lease plus typed
-- calendar projection, source-attestation, and completion receipts.  The
-- generic service-health view previously read ops.run alone, so registering
-- this service created a row that could never move beyond unknown/missing.
--
-- Project the existing evidence read-only.  Only Joe's v1 LIVE workflow can
-- affect this service.  A success is healthy only when the exact projection,
-- attestation, capture challenge, and completion receipt all bind the same job
-- and attempt.  Terminal failures remain failures; missing terminal evidence
-- never becomes green.  No role receives ops.run write authority here and no
-- workflow, activation receipt, or canary definition is changed.

begin;

create or replace view ops.v_service_environment_health as
with latest_run as (
  select distinct on (r.service_id, r.environment)
         r.service_id, r.environment, r.state, r.observed_at, r.expires_at,
         r.run_key, r.failure_class, r.source_kind, r.source_ref,
         r.correlation_id
    from ops.run r
   where r.state in ('succeeded','failed','timed_out','cancelled','skipped')
   order by r.service_id, r.environment, r.observed_at desc
), joe_terminal_job as materialized (
  select j.*
    from ops.job j
   where j.definition_key='calendar-prebrief-projection-joe-daily'
     and j.definition_version=1
     and j.mode='live'
     and j.state in ('succeeded','failed','timed_out','cancelled','dead_lettered')
   order by coalesce(j.ended_at,j.updated_at) desc,
            j.scheduled_for desc,j.created_at desc,j.id desc
   limit 1
), joe_job_evidence as (
  select j.*,
         p.id as projection_receipt_id,
         a.id as attestation_receipt_id,
         c.id as capture_challenge_id,
         completion.id as completion_receipt_id,
         terminal.id as terminal_receipt_id,
         (p.id is not null and a.id is not null and c.id is not null
          and completion.id is not null) as success_evidence_valid
    from joe_terminal_job j
    left join ops.calendar_prebrief_projection_receipt p
      on p.job_id=j.id and p.attempt=j.attempt and p.sponsor='joe'
    left join ops.calendar_prebrief_source_attestation_receipt a
      on a.id=p.source_attestation_id
     and a.job_id=j.id and a.attempt=j.attempt and a.sponsor='joe'
     and a.mode='live' and a.destination='live'
     and a.snapshot_at=p.snapshot_at
     and a.allowlist_revision_id=p.allowlist_revision_id
     and a.allowlist_digest=p.allowlist_digest
    left join ops.calendar_prebrief_capture_challenge c
      on c.id=a.capture_challenge_id
     and c.job_id=j.id and c.attempt=j.attempt and c.sponsor='joe'
     and c.mode='live' and c.destination='live'
     and c.lease_token=a.lease_token
     and c.scheduled_for=j.scheduled_for
     and c.allowlist_revision_id=p.allowlist_revision_id
     and c.allowlist_digest=p.allowlist_digest
    left join lateral (
      select r.id
        from ops.job_receipt r
       where r.job_id=j.id and r.attempt=j.attempt and r.kind='completion'
         and r.receipt_ref='calendar-prebrief:joe:'||j.id::text||':'||j.attempt::text
         and r.evidence=jsonb_build_object(
           'sponsor','joe','mode','live','attestation_id',a.id,
           'receipt_id',p.id,'allowlist_revision_id',p.allowlist_revision_id,
           'allowlist_digest',p.allowlist_digest)
       order by r.created_at desc,r.id desc
       limit 1
    ) completion on true
    left join lateral (
      select r.id
        from ops.job_receipt r
       where r.job_id=j.id and r.attempt=j.attempt
         and ((j.state='failed' and r.kind='failure')
           or (j.state='timed_out' and r.kind in ('timeout','failure'))
           or (j.state='cancelled' and r.kind='override')
           or (j.state='dead_lettered' and r.kind='dead_letter'))
       order by r.created_at desc,r.id desc
       limit 1
    ) terminal on true
), joe_observation as (
  select
    'calendar-prebrief-projection-joe-daily'::text as run_key,
    case
      when j.state='succeeded' and j.success_evidence_valid then 'succeeded'
      when j.state='succeeded' then 'failed'
      else j.state
    end as state,
    case
      when j.state='succeeded' and not j.success_evidence_valid
        then 'calendar_prebrief_completion_evidence_missing'
      when j.state<>'succeeded' and j.terminal_receipt_id is null
        then coalesce(j.last_failure_class,'calendar_prebrief_terminal_evidence_missing')
      else j.last_failure_class
    end as failure_class,
    j.correlation_id,
    coalesce(j.ended_at,j.updated_at) as observed_at,
    null::timestamptz as expires_at,
    case
      when j.state='succeeded' and j.success_evidence_valid
        then 'calendar_prebrief_projection_receipt'
      when j.terminal_receipt_id is not null then 'job_receipt'
      else 'job'
    end as source_kind,
    case
      when j.state='succeeded' and j.success_evidence_valid
        then 'ops.calendar_prebrief_projection_receipt:'||j.projection_receipt_id::text
      when j.terminal_receipt_id is not null
        then 'ops.job_receipt:'||j.terminal_receipt_id::text
      else 'ops.job:'||j.id::text
    end as source_ref
    from joe_job_evidence j
), observation as (
  select
    se.service_id,se.environment,
    case when s.key='calendar-prebrief-joe' then jo.state else lr.state end as state,
    case when s.key='calendar-prebrief-joe' then jo.observed_at else lr.observed_at end as observed_at,
    case when s.key='calendar-prebrief-joe' then jo.expires_at else lr.expires_at end as expires_at,
    case when s.key='calendar-prebrief-joe' then jo.run_key else lr.run_key end as run_key,
    case when s.key='calendar-prebrief-joe' then jo.failure_class else lr.failure_class end as failure_class,
    case when s.key='calendar-prebrief-joe' then jo.source_kind else lr.source_kind end as source_kind,
    case when s.key='calendar-prebrief-joe' then jo.source_ref else lr.source_ref end as source_ref,
    case when s.key='calendar-prebrief-joe' then jo.correlation_id else lr.correlation_id end as correlation_id
    from ops.service_environment se
    join ops.service s on s.id=se.service_id
    left join latest_run lr
      on lr.service_id=se.service_id and lr.environment=se.environment
    left join joe_observation jo on s.key='calendar-prebrief-joe'
   where s.retired_at is null
)
select
  se.service_id,
  s.key as service_key,
  s.name as service_name,
  s.criticality,
  se.environment,
  o.run_key as last_run_key,
  o.state as last_run_state,
  o.failure_class as last_failure_class,
  o.correlation_id as last_correlation_id,
  o.observed_at,
  coalesce(
    o.expires_at,
    o.observed_at + make_interval(secs =>
      se.expected_cadence_seconds + se.cadence_grace_seconds)
  ) as expires_at,
  ops.freshness(
    o.observed_at,
    coalesce(o.expires_at,
             o.observed_at + make_interval(secs =>
               se.expected_cadence_seconds + se.cadence_grace_seconds))
  ) as freshness_state,
  coalesce(o.source_kind,'registry') as source_kind,
  coalesce(o.source_ref,'ops.service_environment') as source_ref,
  case
    when o.state is null then 'unknown'
    when ops.freshness(
           o.observed_at,
           coalesce(o.expires_at,
                    o.observed_at + make_interval(secs =>
                      se.expected_cadence_seconds + se.cadence_grace_seconds))
         ) <> 'fresh' then 'unknown'
    when o.state in ('failed','timed_out','dead_lettered') then 'unavailable'
    when o.state in ('skipped','cancelled') then 'degraded'
    when o.state='succeeded' then 'healthy'
    else 'unknown'
  end as health
  from ops.service_environment se
  join ops.service s on s.id=se.service_id
  join observation o on o.service_id=se.service_id and o.environment=se.environment
 where s.retired_at is null;

comment on view ops.v_service_environment_health is
  'The only place service health is expressed. Ordinary services derive from '
  'ops.run. calendar-prebrief-joe derives read-only from its exact Joe v1 live '
  'ops.job and typed projection/attestation/completion receipts, so its narrow '
  'runtime does not need broad ops.run write authority. Missing or stale '
  'evidence is never green, and unrelated workflows or modes cannot count.';

do $$
declare columns text[];
begin
  select array_agg(column_name order by ordinal_position) into columns
    from information_schema.columns
   where table_schema='ops' and table_name='v_service_environment_health';
  if columns is distinct from array[
      'service_id','service_key','service_name','criticality','environment',
      'last_run_key','last_run_state','last_failure_class',
      'last_correlation_id','observed_at','expires_at','freshness_state',
      'source_kind','source_ref','health'] then
    raise exception '0308 FAILED: service health view contract changed';
  end if;
  if not has_table_privilege('carr_reader','ops.v_service_environment_health','select')
     or not has_table_privilege('carr_writer','ops.v_service_environment_health','select') then
    raise exception '0308 FAILED: service health reader grants changed';
  end if;
end $$;

commit;
