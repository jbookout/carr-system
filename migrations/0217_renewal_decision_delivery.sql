-- 0217_renewal_decision_delivery.sql — the safe, record-native renewal slice
-- for the morning brief.  The old local JSON review artifact contained the
-- raw contact fields needed by the promotion gate.  A morning reader needs to
-- know whether a channel exists, when the lease event is, and whether a T1 row
-- needs review; it never needs a phone number, email, address, opaque source
-- payload, or candidate identifier.

begin;

-- The source's observed time is deliberately computed over *all* current
-- renewal-radar candidates.  An empty T1 set is therefore distinguishable from
-- a source that has never arrived: the status view below returns fresh + zero
-- for the former and missing/stale for the latter.
create or replace view v_renewal_decision_queue as
with renewal_source as (
  select cp.name as display_name,
         cp.org_name,
         cp.vertical,
         cp.city,
         cp.county,
         cp.state,
         cp.est_lease_event,
         case
           when upper(coalesce(cp.source_row->>'tier', '')) like 'T1%' then 't1'
           else 'not_t1'
         end as tier_status,
         case
           when lower(coalesce(cp.source_row->>'flag', '')) like 'already%' then 'already_known'
           when lower(coalesce(cp.source_row->>'flag', '')) like '%not yet tenant-identified%'
             then 'building_signal'
           when nullif(btrim(coalesce(cp.source_row->>'flag', '')), '') is null then 'clear'
           else 'review_required'
         end as flag_status,
         ((cp.email is not null and cp.email <> '') or (cp.phone is not null and cp.phone <> ''))
           as has_channel,
         max(cp.updated_at) over () as source_observed_at
    from candidate_pool cp
   where cp.source = 'renewal-radar'
     and cp.status = 'pool'
)
select display_name,
       org_name,
       vertical,
       city,
       county,
       state,
       est_lease_event,
       tier_status,
       flag_status,
       has_channel,
       count(*) over ()::integer as decision_count,
       source_observed_at,
       case
         when source_observed_at >= now() - interval '36 hours' then 'fresh'
         else 'stale'
       end as freshness_state
  from renewal_source
 where tier_status = 't1';

create or replace view v_renewal_decision_queue_status as
with renewal_source as (
  select cp.updated_at,
         upper(coalesce(cp.source_row->>'tier', '')) like 'T1%' as is_t1
    from candidate_pool cp
   where cp.source = 'renewal-radar'
     and cp.status = 'pool'
), aggregate as (
  select count(*) filter (where is_t1)::integer as t1_candidate_count,
         max(updated_at) as source_observed_at
    from renewal_source
)
select t1_candidate_count,
       source_observed_at,
       case
         when source_observed_at is null then 'missing'
         when source_observed_at >= now() - interval '36 hours' then 'fresh'
         else 'stale'
       end as freshness_state
  from aggregate;

comment on view v_renewal_decision_queue is
  'The safe T1 renewal decision reader for morning delivery. It exposes only display/context, '
  'lease event, normalized T1/flag state, channel existence, count, and source freshness. '
  'Never add candidate IDs, source keys, source_row, email, phone, or address.';

comment on view v_renewal_decision_queue_status is
  'One-row freshness/count companion for v_renewal_decision_queue. Fresh zero is a real empty '
  'T1 queue; missing or stale means the morning brief must be unavailable, never empty.';

revoke all on v_renewal_decision_queue, v_renewal_decision_queue_status from public;
grant select on v_renewal_decision_queue, v_renewal_decision_queue_status to carr_reader;

do $$
declare forbidden text[] := array['id','pool_id','source_key','source_row','email','phone','address'];
begin
  if exists (
    select 1
      from information_schema.columns
     where table_schema='public' and table_name='v_renewal_decision_queue'
       and column_name = any(forbidden)
  ) then
    raise exception '0217 FAILED: renewal decision queue exposes a prohibited raw field';
  end if;
  if not has_table_privilege('carr_reader', 'v_renewal_decision_queue', 'select')
     or not has_table_privilege('carr_reader', 'v_renewal_decision_queue_status', 'select') then
    raise exception '0217 FAILED: reader cannot reach safe renewal decision views';
  end if;
  if has_table_privilege('carr_reader', 'candidate_pool', 'select')
     or has_table_privilege('carr_reader', 'v_export_pool', 'select') then
    raise exception '0217 FAILED: reader has a raw or exporter renewal path';
  end if;
  if has_table_privilege('carr_exporter', 'v_renewal_decision_queue', 'select')
     or has_table_privilege('carr_jobs', 'v_renewal_decision_queue', 'select') then
    raise exception '0217 FAILED: renewal delivery view widened beyond its reader boundary';
  end if;
end $$;

commit;
