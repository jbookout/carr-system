-- 0162_control_plane_input_views.sql
-- Safe, PII-minimized read projections for cognition input construction.
-- The jobs role receives these projections only; scheduler payloads never get
-- to assert queue state, freshness, identity, receipt state, or policy facts.

begin;

create or replace view ops.v_control_plane_capability_candidate as
select w.id, w.state, w.project_context,
       s.id as session_id, s.state as session_state,
       w.program_ordinal
  from ops.v_capability_program_next w
  join lateral (
    select x.id, x.state
      from ops.capability_agent_session x
     where x.work_request_id = w.id and x.state = 'verification'
     order by x.updated_at desc limit 1
  ) s on true;

create or replace view public.v_control_plane_enrichment_queue as
select q.subject_type,
       q.subject_id,
       q.reason as reverification_due,
       'not_current'::text as current_verification_status,
       row_number() over (
         order by q.past_age_floor desc, q.subject_touches desc,
                  q.observed_at asc, q.subject_id
       )::integer as priority,
       coalesce(q.expires_on::timestamptz, q.observed_at) as expired_at
  from public.v_expired_verification q;

comment on view public.v_control_plane_enrichment_queue is
  'Control-plane re-verification projection. Every row is expired or unstamped '
  'volatile evidence, so this view never calls a fact verified or current.';

create or replace view public.v_control_plane_deal_history_queue as
with thursday_enrichment as (
  select j.id as job_id, j.scheduled_for, j.mode,
         (r.evidence->>'subjects_processed')::integer as subjects_processed,
         row_number() over (
           partition by j.mode,
             date_trunc('week', j.scheduled_for at time zone 'America/Chicago')
           order by j.scheduled_for desc, r.created_at desc
         ) as rn
    from ops.job_receipt r
    join ops.job j on j.id=r.job_id
   where j.definition_key='contact-enrichment-weekly'
     and r.kind='completion'
     and extract(isodow from j.scheduled_for at time zone 'America/Chicago')=4
     and jsonb_typeof(r.evidence->'subjects_processed')='number'
     and (r.evidence->>'subjects_processed')::integer >= 0
), weekly as (
  select job_id, scheduled_for, mode, subjects_processed,
         case when subjects_processed >= 30 then 15 else 25 end as slice_limit
    from thursday_enrichment where rn=1
), raw as (
  select 'client'::text as subject_type, c.id as subject_id,
         max(d.created_at) as newest_deal_at
    from public.client c
    join public.party p on p.id = c.party_id
    join public.deal d on d.client_id = c.id
   where d.salesforce_id is not null
     and p.contact_state <> 'do_not_contact'
     and not exists (
       select 1 from public.record_flag f
        where f.subject_type = 'client' and f.subject_id = c.id
          and f.kind = 'verified'
          and (f.expires_on is null or f.expires_on >= current_date)
     )
   group by c.id
  union all
  select 'party'::text, dp.party_id, max(d.created_at)
    from public.deal_participant dp
    join public.deal d on d.id = dp.deal_id
    join public.party p on p.id = dp.party_id
   where d.salesforce_id is not null
     and dp.party_id is not null
     and p.contact_state <> 'do_not_contact'
     and not exists (
       select 1 from public.record_flag f
        where f.subject_type = 'party' and f.subject_id = dp.party_id
          and f.kind = 'verified'
          and (f.expires_on is null or f.expires_on >= current_date)
     )
   group by dp.party_id
), ranked as (
  select r.*, row_number() over (
           order by r.newest_deal_at desc nulls last, r.subject_type, r.subject_id
         )::integer as priority
    from raw r
)
select r.subject_type, r.subject_id, 'unverified'::text as verification,
       r.priority, 'canonical_counterparty'::text as source_class,
       w.slice_limit, w.subjects_processed as enrichment_subject_count,
       w.scheduled_for as enrichment_scheduled_for, w.mode as enrichment_mode
  from ranked r cross join weekly w
 where r.priority <= w.slice_limit;

create or replace view public.v_control_plane_npi_delta as
select p.source_key as lane,
       p.state as source_state,
       p.vertical as source_entity_type,
       null::boolean as territory_match,
       null::text as entity_type,
       'unprocessed'::text as delta_state,
       p.source as source_lane,
       p.created_at
  from public.candidate_pool p
 where p.source = 'npi-sweep'
   and p.status = 'pool';

create or replace view public.v_control_plane_content_fuel_rotation as
select lane, temperature, null::text as source_class,
       'versioned_rotation_policy_only'::text as evidence_state
  from (values
    ('local-healthcare'::text, 'local'::text),
    ('rotating-cold-lane'::text, 'cold'::text)
  ) policy(lane, temperature);

create or replace view public.v_control_plane_radar_candidates as
select p.source as lane,
       p.score,
       p.updated_at,
       p.est_lease_event
  from public.candidate_pool p
 where p.source in ('corp-filings', 'upstream', 'renewal-radar')
   and p.status = 'pool'
   and p.score is not null;

create or replace view public.v_control_plane_idea_candidates as
select c.id::text as id,
       coalesce(nullif(c.topic, ''), c.kind) as title,
       max(coalesce(p.live_at, p.scheduled_at)) as last_surfaced
  from public.content_piece c
  left join public.placement p on p.piece_id = c.id
 where c.status in ('idea', 'drafted', 'in_review', 'approved', 'edited_approved')
 group by c.id, c.topic, c.kind;

create or replace view public.v_control_plane_social_sources as
select 'content:' || c.id::text as source_ref
  from public.content_piece c
 where c.status in ('idea', 'drafted', 'in_review', 'approved', 'edited_approved')
 order by c.updated_at desc;

create or replace view public.v_control_plane_social_coverage as
select p.scheduled_at
  from public.placement p
 where p.scheduled_at is not null;

-- Metrics stay fail-closed until the owned-account registry is implemented:
-- canonical metric values and placement timestamps are projected, but no row
-- is relabeled as belonging to an owned account merely because it is present.
create or replace view public.v_control_plane_social_metric_exports as
select m.placement_id,
       p.external_id,
       p.platform,
       m.observed_at as source_observed_at,
       m.kind as metric_kind,
       m.value as metric_value,
       p.live_at,
       p.scheduled_at,
       null::boolean as owned_account
  from public.v_placement_metric_latest m
  join public.placement p on p.id=m.placement_id
 where p.external_id is not null;

create or replace view ops.v_control_plane_health_evidence as
select 'live'::text as evidence_class,
       'health:' || h.service_key || ':' || h.environment as source_ref,
       h.observed_at
  from ops.v_service_environment_health h
 where h.health = 'healthy'
union all
select 'registry', 'service:' || s.key, s.updated_at
  from ops.service s where s.retired_at is null
union all
select 'artifact', 'release:' || r.release_key || ':' || r.artifact_digest,
       r.observed_at
  from ops.release r
 where r.state = 'complete' and r.artifact_digest is not null;

create or replace view ops.v_control_plane_actionable_loops as
select l.loop_id as id,
       l.owner,
       'actionable'::text as state,
       null::text as counterparty_ref,
       null::text as event_blocker_ref
  from public.v_loops l
 where l.status = 'open'
   and lower(coalesce(l.owner, '')) = 'system'
   and l.personal_to is null
   and l.due_on is null;

create or replace view ops.v_control_plane_doctrine_due as
select s.id, d.slug, s.review_after
  from public.doctrine_section s
  join public.doctrine_document d on d.id = s.document_id
 where s.status = 'active' and s.review_after <= now();

create or replace view ops.v_control_plane_doctrine_failures as
select 'doctrine-gate:' || f.run_id::text || ':' || f.check_key as source_ref,
       r.started_at as observed_at
  from public.doctrine_gate_finding f
  join public.doctrine_gate_run r on r.id = f.run_id
 where not f.passed
   and r.started_at >= date_trunc('month', now());

create or replace view ops.v_control_plane_system_prune_candidates as
select d.object_kind || ':' || d.object_name as subject_ref,
       'stale'::text as measurement
  from public.deprecation d
 where d.dropped_at is null
   and d.safe_to_drop_after is not null
   and d.safe_to_drop_after <= current_date;

grant select on public.v_control_plane_enrichment_queue,
                public.v_control_plane_deal_history_queue,
                public.v_control_plane_content_fuel_rotation,
                public.v_control_plane_npi_delta,
                public.v_control_plane_radar_candidates,
                public.v_control_plane_idea_candidates,
                public.v_control_plane_social_sources,
                public.v_control_plane_social_coverage,
                public.v_control_plane_social_metric_exports
  to carr_jobs;

-- These are definer views used as deliberately narrow projections.  An
-- installation with altered default ACLs must not accidentally expose them to
-- PUBLIC simply because it creates a new view.
revoke all on public.v_control_plane_enrichment_queue,
              public.v_control_plane_deal_history_queue,
              public.v_control_plane_content_fuel_rotation,
              public.v_control_plane_npi_delta,
              public.v_control_plane_radar_candidates,
              public.v_control_plane_idea_candidates,
              public.v_control_plane_social_sources,
              public.v_control_plane_social_coverage,
              public.v_control_plane_social_metric_exports,
              ops.v_control_plane_health_evidence,
              ops.v_control_plane_capability_candidate,
              ops.v_control_plane_actionable_loops,
              ops.v_control_plane_doctrine_due,
              ops.v_control_plane_doctrine_failures,
              ops.v_control_plane_system_prune_candidates
  from public;
grant select on ops.v_control_plane_health_evidence,
                ops.v_control_plane_capability_candidate,
                ops.v_control_plane_actionable_loops,
                ops.v_control_plane_doctrine_due,
                ops.v_control_plane_doctrine_failures,
                ops.v_control_plane_system_prune_candidates
  to carr_jobs;

-- 0159 temporarily granted the runner broad source tables while the typed
-- projections did not exist. Revoke that bridge now: input collection needs
-- stable identifiers and policy facts, not raw candidate/contact payloads.
revoke select on public.v_expired_verification, public.candidate_pool,
                 public.content_piece, public.placement, public.v_loops
  from carr_jobs;

do $$
declare view_name text;
begin
  foreach view_name in array array[
    'public.v_control_plane_enrichment_queue',
    'public.v_control_plane_deal_history_queue',
    'public.v_control_plane_content_fuel_rotation',
    'public.v_control_plane_npi_delta',
    'public.v_control_plane_radar_candidates',
    'public.v_control_plane_idea_candidates',
    'public.v_control_plane_social_sources',
    'public.v_control_plane_social_coverage',
    'public.v_control_plane_social_metric_exports',
    'ops.v_control_plane_health_evidence',
    'ops.v_control_plane_capability_candidate',
    'ops.v_control_plane_actionable_loops',
    'ops.v_control_plane_doctrine_due',
    'ops.v_control_plane_doctrine_failures',
    'ops.v_control_plane_system_prune_candidates'
  ] loop
    if to_regclass(view_name) is null then
      raise exception '0162 FAILED: missing input view %', view_name;
    end if;
    if not has_table_privilege('carr_jobs', view_name, 'select') then
      raise exception '0162 FAILED: jobs role cannot read %', view_name;
    end if;
    if exists (
      select 1 from pg_class c
       cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
      where c.oid=view_name::regclass and acl.grantee=0 and acl.privilege_type='SELECT'
    ) then
      raise exception '0162 FAILED: PUBLIC can read collector projection %', view_name;
    end if;
  end loop;
  foreach view_name in array array[
    'public.v_expired_verification', 'public.candidate_pool',
    'public.content_piece', 'public.placement', 'public.v_loops'
  ] loop
    if has_table_privilege('carr_jobs', view_name, 'select') then
      raise exception '0162 FAILED: jobs role still has broad source read %', view_name;
    end if;
  end loop;
end $$;

commit;
