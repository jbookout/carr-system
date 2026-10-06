-- rollback: drop the five v_routine_* views; no base-table data or mutation privileges change.
-- expand-contract: expand — scoped read views support code-owned routines with the existing carr_jobs credential.
create or replace view public.v_routine_contact_inputs
with (security_barrier=true) as
with hydrated as (
 select q.priority,q.subject_type,q.subject_id::text,
        coalesce(r.ref,p.ref) as ref,p.id::text as party_id,p.name,
        p.contact_state,p.merged_into::text,p.title,p.email,p.phone,p.cell,
        p.city,p.county,p.state,p.npi,p.specialty,p.version as party_version,
        org.name as company,v.category_slug,v.verticals,v.version as vendor_version,
        q.reverification_due,
        row_number() over (partition by p.id order by q.priority) as person_rank
 from public.v_control_plane_enrichment_queue q
 left join public.v_ref_index r on r.subject_type=q.subject_type and r.subject_id=q.subject_id
 join public.party p on p.id=case when q.subject_type='party' then q.subject_id else r.party_id end
 left join public.party org on org.id=p.org_id
 left join public.vendor v on q.subject_type='vendor' and v.id=q.subject_id
 where not coalesce(r.merged,false) and p.merged_into is null and p.deleted_at is null
   and p.contact_state <> 'do_not_contact'
   and not exists (
     select 1 from public.record_flag attempt
      where attempt.subject_type='party' and attempt.subject_id=p.id
        and attempt.kind='contact_enrichment_attempt'
        and attempt.expires_on > current_date
   )
)
select * from hydrated where person_rank=1 order by priority limit 40;

create or replace view public.v_routine_contact_party
with (security_barrier=true) as
select id,version,contact_state,merged_into from public.party where deleted_at is null;

create or replace view public.v_routine_contact_vendor
with (security_barrier=true) as
select id,version,merged_into from public.vendor;

create or replace view public.v_routine_vendor_category
with (security_barrier=true) as
select slug,label,sort from public.vendor_category;

create or replace view public.v_routine_effect_receipts
with (security_barrier=true) as
select idempotency_key,verb,response from public.tool_call
 where verb in ('record-finding','update-party-contact','update-vendor','report-problem',
                'add-party','new-lead','add-loop');

revoke all on public.v_routine_contact_inputs,public.v_routine_contact_party,
 public.v_routine_contact_vendor,public.v_routine_vendor_category,
 public.v_routine_effect_receipts from public;
grant select on public.v_routine_contact_inputs,public.v_routine_contact_party,
 public.v_routine_contact_vendor,public.v_routine_vendor_category,
 public.v_routine_effect_receipts to carr_jobs;

comment on view public.v_routine_contact_inputs is
 'Code routine contact inputs: existing five-band enrichment priority, one person per slice, cap 40, contact exclusion and 30-day research retry receipt.';
comment on view public.v_routine_effect_receipts is
 'Interrupted routine effect readback: only routine effect verbs and their envelope response, without other tool replies or credential columns.';
