begin;

alter table capture_session
  add column if not exists post_call boolean not null default false;

create table capture_post_call_candidate (
  id                 uuid primary key default gen_random_uuid(),
  session_id         uuid not null references capture_session(id),
  idempotency_key    text not null,
  batch_hash         text not null,
  item_index         integer not null check (item_index >= 0),
  kind               text not null check (kind in ('assigned_action','email_draft')),
  deal_id            uuid not null references deal(id),
  assignee_slug      text,
  action_description text,
  due_on             date,
  recipient_party_id uuid references party(id),
  recipient_ref      text,
  email_subject      text,
  body_sha256        text,
  evidence_quote     text not null check (array_length(regexp_split_to_array(btrim(evidence_quote), '\s+'), 1) <= 15),
  confidence         numeric not null check (confidence >= 0 and confidence <= 1),
  status             text not null default 'pending' check (status in ('pending','confirmed','skipped')),
  resolved_by        uuid references actor(id),
  resolution_note    text,
  resulting_ref      text,
  created_at         timestamptz not null default now(),
  resolved_at        timestamptz,
  unique (session_id, idempotency_key, item_index),
  check (
    (kind = 'assigned_action'
      and assignee_slug in ('joe','dell')
      and action_description is not null and btrim(action_description) <> ''
      and recipient_party_id is null and recipient_ref is null
      and email_subject is null and body_sha256 is null)
    or
    (kind = 'email_draft'
      and assignee_slug is null and action_description is null and due_on is null
      and recipient_party_id is not null and recipient_ref ~ '^P-[0-9]+$'
      and email_subject is not null and btrim(email_subject) <> ''
      and body_sha256 ~ '^[0-9a-f]{64}$')
  ),
  check (
    (status = 'pending' and resolved_by is null and resolved_at is null and resulting_ref is null)
    or (status = 'skipped' and resolved_by is not null and resolved_at is not null and resulting_ref is null)
    or (status = 'confirmed' and resolved_by is not null and resolved_at is not null
        and (resulting_ref is not null or kind = 'email_draft'))
  )
);

create index capture_post_call_candidate_session_idx
  on capture_post_call_candidate (session_id, status, created_at, id);

create table capture_post_call_report (
  session_id       uuid primary key references capture_session(id),
  idempotency_key  text not null,
  report_sha256    text not null check (report_sha256 ~ '^[0-9a-f]{64}$'),
  candidate_count  integer not null check (candidate_count >= 0),
  filed_at         timestamptz not null default now(),
  unique (session_id, idempotency_key)
);

create table capture_post_call_action (
  id           uuid primary key default gen_random_uuid(),
  candidate_id uuid not null unique references capture_post_call_candidate(id),
  deal_id      uuid not null references deal(id),
  owner_id     uuid not null references actor(id),
  due_on       date,
  description  text not null check (btrim(description) <> ''),
  accepted_by  uuid not null references actor(id),
  accepted_at  timestamptz not null default now(),
  status       text not null default 'open' check (status in ('open','done','dropped')),
  updated_at   timestamptz not null default now(),
  completed_at timestamptz,
  check ((status = 'done' and completed_at is not null)
      or (status in ('open','dropped') and completed_at is null))
);

create function capture_call_context(requested_deal_ids uuid[])
returns table (
  deal_id uuid,
  deal_name text,
  owner text,
  operating_state text,
  participant_party_id uuid,
  participant_party_ref text,
  participant_name text,
  participant_email text,
  participant_role text
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select d.id, d.name, d.owner, d.operating_state, dp.party_id, p.ref, p.name, p.email, dp.role
    from public.deal d
    left join public.deal_participant dp on dp.deal_id=d.id and dp.to_at is null
    left join public.party p on p.id=dp.party_id
   where d.id = any(requested_deal_ids)
     and d.outcome is null
     and d.operating_state = 'active'
$$;

revoke all on function capture_call_context(uuid[]) from public;

create or replace view v_deal_room_action as
select n.id, n.subject_id as deal_id, a.slug as owner, n.description,
       n.due_on, n.status, n.updated_at
  from next_action n join actor a on a.id=n.owner_id
 where n.subject_type='deal'
union all
select pca.id, pca.deal_id, a.slug, pca.description, pca.due_on,
       pca.status, pca.updated_at
  from capture_post_call_action pca join actor a on a.id=pca.owner_id;

create or replace view v_today_triage as
  select 'next_action'::text as item_kind, na.id, na.subject_type, na.subject_id,
         owner.slug as owner, na.description as what, na.due_on,
         coalesce(r.display_name, r.org_name) as subject_name,
         r.ref as subject_ref,
         carr_business_days(na.due_on, current_date) as business_days_overdue
    from next_action na
    join actor owner on owner.id = na.owner_id
    left join v_ref_index r on r.subject_id = na.subject_id and r.subject_type = na.subject_type
   where na.status = 'open' and na.due_on is not null and na.due_on <= current_date
     and (na.hold_until is null or na.hold_until <= current_date)
union all
  select 'post_call_action'::text, pca.id, 'deal'::text, pca.deal_id,
         owner.slug, pca.description, pca.due_on,
         coalesce(r.display_name, r.org_name), r.ref,
         carr_business_days(pca.due_on, current_date)
    from capture_post_call_action pca
    join actor owner on owner.id = pca.owner_id
    left join v_ref_index r on r.subject_id = pca.deal_id and r.subject_type = 'deal'
   where pca.status = 'open' and pca.due_on is not null and pca.due_on <= current_date
union all
  select 'critical_date'::text, cd.id, 'deal'::text, cd.deal_id, null::text,
         cd.kind || coalesce(': ' || cd.note, ''), cd.due_on,
         coalesce(r.display_name, r.org_name), r.ref,
         carr_business_days(cd.due_on, current_date)
    from critical_date cd
    left join v_ref_index r on r.subject_id = cd.deal_id and r.subject_type = 'deal'
   where cd.status = 'open' and cd.due_on <= (current_date + 14)
union all
  select 'ingest'::text, i.id, 'inbox'::text, i.id, null::text,
         coalesce(nullif(trim(i.payload->>'summary'), ''), nullif(trim(i.payload->>'title'), ''),
                  nullif(trim(i.payload->>'subject'), ''), i.source || ' item awaiting triage'),
         i.received_at::date, nullif(trim(i.payload->>'organizer'), ''), null::text,
         carr_business_days(i.received_at::date, current_date)
    from ingest_inbox i where i.status = 'new';

comment on table capture_post_call_candidate is
  'Remote-safe Call Mode proposals. Transcript and Outlook draft bodies are local only; this table stores exact refs, action or draft metadata, a hash, and short evidence.';
comment on table capture_post_call_report is
  'Aggregate post-call filing marker. It contains only a local report hash and candidate count.';
comment on table capture_post_call_action is
  'Additive, human-accepted Call Mode action. It never replaces or drops an existing next_action.';

grant select, insert, update on capture_post_call_candidate, capture_post_call_report, capture_post_call_action to carr_writer;
grant execute on function capture_call_context(uuid[]) to carr_reader, carr_writer;

commit;
