-- 0305_deal_history_queue_receipt_missing_visibility.sql
--
-- An absent contact-enrichment receipt must not make an unfinished
-- deal-history backlog appear empty.  It also cannot manufacture a run cap:
-- only the typed Thursday completion receipt proves the 15/25 sizing decision.

begin;

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
), sizing as (
  select w.job_id, w.scheduled_for, w.mode, w.subjects_processed,
         w.slice_limit, 'receipt_bound'::text as sizing_state
    from weekly w
  union all
  select null::uuid, null::timestamptz, null::text, null::integer,
         null::integer, 'receipt_missing'::text
   where not exists (select 1 from weekly)
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
       s.slice_limit, s.subjects_processed as enrichment_subject_count,
       s.scheduled_for as enrichment_scheduled_for, s.mode as enrichment_mode,
       s.sizing_state
  from ranked r cross join sizing s
 where s.slice_limit is null or r.priority <= s.slice_limit;

comment on view public.v_control_plane_deal_history_queue is
  'Deal-history research queue. receipt_missing keeps an unverified backlog '
  'visible when no typed Thursday completion receipt exists, but supplies no '
  'execution cap or receipt metadata. receipt_bound rows carry the exact 15/25 '
  'cap and receipt evidence required by the execution collector.';

grant select on public.v_control_plane_deal_history_queue to carr_jobs;

commit;
