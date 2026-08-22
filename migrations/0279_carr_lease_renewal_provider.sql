-- 0279_carr_lease_renewal_provider.sql
--
-- The renewal lane's authenticated canonical provider is CARR's own executed
-- lease/abstract ledger.  Market-location datasets are useful enrichment, but
-- they cannot establish a lease expiration and must never make a T1 renewal.
-- A current row is therefore eligible only when a human partner recorded an
-- exact expiration with bounded first-party evidence through the writer verb.

begin;

alter table lease add column owner_id uuid references actor(id);
alter table lease add column evidence_kind text;
alter table lease add column evidence_ref text;
alter table lease add column status text not null default 'legacy_unverified';
alter table lease add column superseded_at timestamptz;
alter table lease add column supersedes_lease_id uuid references lease(id);

alter table lease add constraint lease_status_check
  check (status in ('legacy_unverified','current','superseded'));
alter table lease add constraint lease_evidence_kind_check
  check (evidence_kind is null or evidence_kind in
    ('executed_lease','lease_amendment','lease_abstract'));
alter table lease add constraint lease_current_provider_contract_check
  check (status <> 'current' or
    (deal_id is not null and client_id is not null and owner_id is not null and
     executed_on is not null and expiration_on is not null and
     evidence_kind is not null and nullif(btrim(evidence_ref),'') is not null and
     nullif(btrim(source),'') is not null and
     (commencement_on is null or expiration_on > commencement_on))) not valid;

create unique index lease_one_current_per_deal
  on lease(deal_id) where status='current';
create index lease_renewal_owner_expiration_idx
  on lease(owner_id,expiration_on) where status='current';

create or replace function ops.record_executed_lease(
  p_deal text,
  p_base_version integer,
  p_executed_on date,
  p_commencement_on date,
  p_expiration_on date,
  p_term_months integer,
  p_evidence_kind text,
  p_evidence_ref text,
  p_source text
) returns table(
  lease_id uuid,version integer,superseded_lease_id uuid,deal_id uuid,client_id uuid
)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_deal_ids uuid[];
  v_deal_id uuid;
  v_client_id uuid;
  v_owner_id uuid;
  v_current_id uuid;
  v_current_version integer;
  v_new_id uuid;
  v_new_version integer;
begin
  v_actor_slug:=ops.authority_actor_slug();
  select id into v_actor_id from actor where slug=v_actor_slug and active;
  if v_actor_id is null then raise exception 'lease authority actor is unavailable'; end if;
  if nullif(btrim(coalesce(p_deal,'')),'') is null then raise exception 'deal is required'; end if;
  if p_deal ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select array_agg(id) into v_deal_ids from deal where id=p_deal::uuid;
  else
    select array_agg(id order by id) into v_deal_ids from deal where lower(name)=lower(btrim(p_deal));
  end if;
  if coalesce(array_length(v_deal_ids,1),0)=0 then raise exception 'lease deal was not found'; end if;
  if array_length(v_deal_ids,1)<>1 then raise exception 'lease deal needs exact disambiguation'; end if;
  v_deal_id:=v_deal_ids[1];

  perform pg_advisory_xact_lock(hashtextextended('executed-lease:'||v_deal_id::text,0));
  select d.client_id,
         coalesce((select dp.actor_id from deal_participant dp
                    where dp.deal_id=d.id and dp.role='lead' and dp.to_at is null limit 1),
                  c.created_by)
    into v_client_id,v_owner_id
    from deal d join client c on c.id=d.client_id
   where d.id=v_deal_id;
  if v_owner_id is distinct from v_actor_id then
    raise exception 'lease authority does not own the current deal';
  end if;
  if p_executed_on is null or p_expiration_on is null then
    raise exception 'executed_on and expiration_on are required';
  end if;
  if p_commencement_on is not null and p_expiration_on<=p_commencement_on then
    raise exception 'lease expiration must follow commencement';
  end if;
  if p_term_months is not null and (p_term_months<1 or p_term_months>480) then
    raise exception 'lease term_months is outside 1..480';
  end if;
  if p_evidence_kind not in ('executed_lease','lease_amendment','lease_abstract') then
    raise exception 'lease evidence kind is not admitted';
  end if;
  if nullif(btrim(coalesce(p_evidence_ref,'')),'') is null or length(p_evidence_ref)>1000 then
    raise exception 'lease evidence reference is required and bounded';
  end if;
  if nullif(btrim(coalesce(p_source,'')),'') is null or length(p_source)>500 then
    raise exception 'lease source is required and bounded';
  end if;

  select l.id,l.version into v_current_id,v_current_version
    from lease l where l.deal_id=v_deal_id and l.status='current' for update;
  if v_current_id is not null and p_base_version is distinct from v_current_version then
    raise exception 'lease version conflict: expected %',v_current_version;
  end if;
  if v_current_id is null and p_base_version is not null then
    raise exception 'no current lease exists for supplied base version';
  end if;
  if v_current_id is not null then
    update lease as held set status='superseded',superseded_at=now(),updated_by=v_actor_id
     where held.id=v_current_id and held.version=v_current_version and held.status='current';
    if not found then raise exception 'lease version conflict during replacement'; end if;
  end if;
  insert into lease
    (deal_id,client_id,owner_id,executed_on,commencement_on,expiration_on,term_months,
     evidence_kind,evidence_ref,source,created_by,updated_by,status,supersedes_lease_id)
  values
    (v_deal_id,v_client_id,v_actor_id,p_executed_on,p_commencement_on,p_expiration_on,p_term_months,
     p_evidence_kind,btrim(p_evidence_ref),btrim(p_source),v_actor_id,v_actor_id,'current',v_current_id)
  returning id,lease.version into v_new_id,v_new_version;
  lease_id:=v_new_id; version:=v_new_version; superseded_lease_id:=v_current_id;
  deal_id:=v_deal_id; client_id:=v_client_id;
  return next;
end $$;

revoke insert,update on lease from carr_writer;
revoke all on function ops.record_executed_lease(text,integer,date,date,date,integer,text,text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_exporter;
grant execute on function ops.record_executed_lease(text,integer,date,date,date,integer,text,text,text)
  to carr_authority;

drop view if exists v_renewal_decision_queue;
drop view if exists v_renewal_decision_queue_status;

create view v_renewal_decision_queue as
select p.name as display_name,
       org.name as org_name,
       c.vertical,
       p.city,
       null::text as county,
       p.state,
       l.expiration_on as est_lease_event,
       't1'::text as tier_status,
       'clear'::text as flag_status,
       ((p.email is not null and p.email<>'') or
        (p.phone is not null and p.phone<>'')) as has_channel,
       count(*) over(partition by owner.slug)::integer as decision_count,
       greatest(l.created_at,l.updated_at) as source_observed_at,
       'ready'::text as freshness_state,
       owner.slug as owner_slug
  from lease l
  join deal d on d.id=l.deal_id and d.client_id=l.client_id
  join client c on c.id=l.client_id
  join party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null
  left join party org on org.id=p.org_id and org.merged_into is null and org.deleted_at is null
  join actor owner on owner.id=coalesce(
    (select dp.actor_id from deal_participant dp
      where dp.deal_id=d.id and dp.role='lead' and dp.to_at is null limit 1),
    c.created_by) and owner.slug in ('joe','dell')
 where l.status='current'
   and l.expiration_on between current_date-90 and current_date+548
   and l.evidence_kind in ('executed_lease','lease_amendment','lease_abstract')
   and nullif(btrim(l.evidence_ref),'') is not null
   and nullif(btrim(l.source),'') is not null;

create view v_renewal_decision_queue_status as
select a.slug as owner_slug,
       count(q.display_name)::integer as t1_candidate_count,
       max(q.source_observed_at) as source_observed_at,
       case when count(q.display_name)=0 then 'empty' else 'ready' end::text as freshness_state
  from actor a
  left join v_renewal_decision_queue q on q.owner_slug=a.slug
 where a.slug in ('joe','dell')
 group by a.slug;

comment on view v_renewal_decision_queue is
  'Safe, sponsor-scoped T1 renewals from current CARR-held executed lease/abstract facts only. Never add IDs, evidence refs, source text, contacts, addresses, or raw document data.';
comment on view v_renewal_decision_queue_status is
  'Per-partner readiness for the authenticated CARR lease ledger. Empty is an explicit true zero; a missing view/query is unavailable at the MCP boundary.';

revoke all on v_renewal_decision_queue,v_renewal_decision_queue_status from public;
grant select on v_renewal_decision_queue,v_renewal_decision_queue_status to carr_reader;

do $$
declare forbidden text[] := array['id','lease_id','deal_id','client_id','party_id','evidence_ref',
  'source','email','phone','address','doc_attachment','options_note'];
begin
  if exists (
    select 1 from information_schema.columns
     where table_schema='public'
       and table_name in ('v_renewal_decision_queue','v_renewal_decision_queue_status')
       and column_name=any(forbidden)
  ) then
    raise exception '0279 FAILED: renewal lease provider exposes a prohibited field';
  end if;
  if not has_table_privilege('carr_reader','v_renewal_decision_queue','select')
     or not has_table_privilege('carr_reader','v_renewal_decision_queue_status','select')
     or has_table_privilege('carr_reader','lease','select') then
    raise exception '0279 FAILED: renewal lease provider reader boundary is wrong';
  end if;
  if has_table_privilege('carr_writer','lease','insert')
     or has_table_privilege('carr_writer','lease','update')
     or has_function_privilege('carr_writer',
       'ops.record_executed_lease(text,integer,date,date,date,integer,text,text,text)','execute')
     or not has_function_privilege('carr_authority',
       'ops.record_executed_lease(text,integer,date,date,date,integer,text,text,text)','execute') then
    raise exception '0279 FAILED: authenticated lease writer boundary is wrong';
  end if;
end $$;

commit;
