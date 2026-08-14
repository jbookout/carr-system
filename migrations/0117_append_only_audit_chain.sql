-- 0117_append_only_audit_chain.sql
--
-- Turn the event spine into an append-only, tamper-evident record without
-- retaining prompts, transcripts, or raw tool responses.  The chain begins at
-- activation: historical events are deliberately not backfilled because a
-- synthetic old chain would falsely claim it existed when those writes landed.

begin;

create extension if not exists pgcrypto;

-- The authorization class answers who the principal is.  The operational
-- profile answers which capability envelope was active on this call.  They are
-- separate facts: a sponsored Codex actor may run under full, capture, or read.
alter table event add column if not exists operational_profile text;
alter table tool_call add column if not exists operational_profile text;
alter table tool_read_call add column if not exists operational_profile text;

comment on column event.operational_profile is
  'Server-selected capability profile active for this write (full/capture/away/read/probe/reviewer). '
  'Null means the event predates 0117 or came through a legacy direct writer whose profile was unknowable.';
comment on column tool_call.operational_profile is
  'Capability envelope active for this mutation, distinct from authorization_class and sponsor.';
comment on column tool_read_call.operational_profile is
  'Capability envelope active for this read, distinct from authorization_class and sponsor.';

create table audit_ledger (
  seq                    bigint generated always as identity primary key,
  id                     uuid not null unique default gen_random_uuid(),
  event_id               uuid not null unique references event(id),
  organization_tenant_id text not null,
  event_recorded_at      timestamptz not null,
  actor_id               uuid not null references actor(id),
  verb                   text not null,
  subject_type           text not null,
  subject_id             uuid not null,
  authorization_class    text,
  operational_profile    text,
  via                    text,
  client_id              text,
  payload_digest         text not null check (payload_digest ~ '^[0-9a-f]{64}$'),
  previous_hash          text not null check (previous_hash ~ '^[0-9a-f]{64}$'),
  entry_hash             text not null unique check (entry_hash ~ '^[0-9a-f]{64}$'),
  chained_at             timestamptz not null default clock_timestamp()
);

create index audit_ledger_tenant_seq_idx
  on audit_ledger (organization_tenant_id, seq desc);
create index audit_ledger_subject_idx
  on audit_ledger (subject_type, subject_id, seq desc);

comment on table audit_ledger is
  'Append-only SHA-256 chain over events written after 0117. The visible columns are routing metadata; '
  'payload_digest binds the full event row, including redacted old/new values and provenance, without '
  'copying business content into a second store. One chain per organization_tenant_id.';

create or replace function audit_event_payload_digest(p_event event)
returns text
language sql
stable
strict
set search_path = pg_catalog, public
as $$
  select encode(digest(convert_to(jsonb_build_object(
    'id',                  (p_event).id,
    -- Epoch numerics are session-independent. Direct timestamptz JSON output
    -- follows the verifier session's TimeZone and would make an intact event
    -- appear changed when checked from a differently configured connection.
    'occurred_at_epoch',   extract(epoch from (p_event).occurred_at),
    'recorded_at_epoch',   extract(epoch from (p_event).recorded_at),
    'actor_id',            (p_event).actor_id,
    'verb',                (p_event).verb,
    'subject_type',        (p_event).subject_type,
    'subject_id',          (p_event).subject_id,
    'field',               (p_event).field,
    'old_value',           (p_event).old_value,
    'new_value',           (p_event).new_value,
    'cause',               (p_event).cause,
    'human_quote',         (p_event).human_quote,
    'agent_rationale',     (p_event).agent_rationale,
    'idempotency_key',     (p_event).idempotency_key,
    'via',                 (p_event).via,
    'client_id',           (p_event).client_id,
    'organization_tenant_id', (p_event).organization_tenant_id,
    'sponsoring_human_slug',  (p_event).sponsoring_human_slug,
    'personal_scope',         (p_event).personal_scope,
    'authorization_class',    (p_event).authorization_class,
    'operational_profile',    (p_event).operational_profile
  )::text, 'UTF8'), 'sha256'), 'hex')
$$;

create or replace function audit_chain_entry_hash(
  p_previous_hash text, p_payload_digest text, p_event_id uuid, p_tenant text
) returns text
language sql
immutable
strict
set search_path = pg_catalog, public
as $$
  select encode(digest(convert_to(
    p_previous_hash || ':' || p_payload_digest || ':' || p_event_id::text || ':' || p_tenant,
    'UTF8'), 'sha256'), 'hex')
$$;

create or replace function append_event_to_audit_chain()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_tenant text := coalesce(new.organization_tenant_id, 'unknown');
  v_previous text;
  v_payload text;
begin
  -- One writer per tenant chain. The event transaction holds this lock through
  -- commit, so two concurrent events cannot choose the same predecessor.
  perform pg_advisory_xact_lock(hashtextextended('carr:audit:' || v_tenant, 0));

  select entry_hash into v_previous
    from audit_ledger
   where organization_tenant_id = v_tenant
   order by seq desc limit 1;
  v_previous := coalesce(v_previous, repeat('0', 64));
  v_payload := audit_event_payload_digest(new);

  insert into audit_ledger (
    event_id, organization_tenant_id, event_recorded_at, actor_id, verb,
    subject_type, subject_id, authorization_class, operational_profile,
    via, client_id, payload_digest, previous_hash, entry_hash
  ) values (
    new.id, v_tenant, new.recorded_at, new.actor_id, new.verb,
    new.subject_type, new.subject_id, new.authorization_class,
    new.operational_profile, new.via, new.client_id, v_payload, v_previous,
    audit_chain_entry_hash(v_previous, v_payload, new.id, v_tenant)
  );
  return new;
end
$$;

revoke all on function append_event_to_audit_chain() from public;

create trigger event_append_audit_chain
after insert on event
for each row execute function append_event_to_audit_chain();

create or replace function refuse_append_only_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  raise exception '% is append-only; record a correcting event instead of %',
    tg_table_name, lower(tg_op)
    using errcode = '55000';
end
$$;

create trigger event_append_only
before update or delete on event
for each row execute function refuse_append_only_mutation();

create trigger audit_ledger_append_only
before update or delete on audit_ledger
for each row execute function refuse_append_only_mutation();

-- The old bundle grant allowed UPDATE on every table. No live verb needs it
-- after update-decision and detach-decision become projection events in this
-- release. Removing the grant makes append-only true even when a caller never
-- reaches the trigger.
revoke update, delete on event from carr_writer;
revoke update, delete on event from carr_jobs;
revoke all on audit_ledger from public, carr_reader, carr_writer, carr_jobs, carr_exporter;

create or replace view v_audit_chain_entry as
with checked as (
  select l.*,
         coalesce(lag(l.entry_hash) over (
           partition by l.organization_tenant_id order by l.seq
         ), repeat('0', 64)) as expected_previous_hash,
         audit_event_payload_digest(e) as expected_payload_digest,
         audit_chain_entry_hash(l.previous_hash, l.payload_digest, l.event_id,
                                l.organization_tenant_id) as expected_entry_hash,
         l.organization_tenant_id is not distinct from coalesce(e.organization_tenant_id, 'unknown')
           and l.event_recorded_at is not distinct from e.recorded_at
           and l.actor_id is not distinct from e.actor_id
           and l.verb is not distinct from e.verb
           and l.subject_type is not distinct from e.subject_type
           and l.subject_id is not distinct from e.subject_id
           and l.authorization_class is not distinct from e.authorization_class
           and l.operational_profile is not distinct from e.operational_profile
           and l.via is not distinct from e.via
           and l.client_id is not distinct from e.client_id
           as metadata_ok
    from audit_ledger l
    join event e on e.id = l.event_id
)
select seq, id, event_id, organization_tenant_id, event_recorded_at,
       actor_id, verb, subject_type, subject_id, authorization_class,
       operational_profile, via, client_id, payload_digest, previous_hash,
       entry_hash, chained_at,
       previous_hash = expected_previous_hash as predecessor_ok,
       payload_digest = expected_payload_digest as payload_ok,
       entry_hash = expected_entry_hash as entry_hash_ok,
       metadata_ok
  from checked;

create or replace view v_audit_chain_status as
select organization_tenant_id,
       count(*)::bigint as entry_count,
       count(*) filter (where not predecessor_ok)::bigint as predecessor_breaks,
       count(*) filter (where not payload_ok)::bigint as payload_breaks,
       count(*) filter (where not entry_hash_ok)::bigint as entry_hash_breaks,
       count(*) filter (where not metadata_ok)::bigint as metadata_breaks,
       bool_and(predecessor_ok and payload_ok and entry_hash_ok and metadata_ok) as chain_ok,
       max(seq) as head_seq,
       max(chained_at) as checked_through
  from v_audit_chain_entry
 group by organization_tenant_id;

-- Raw commitments are an internal verifier detail. Exposing an unkeyed digest
-- of quotes or business JSON would let a reader confirm guessed low-entropy
-- text offline. Reader credentials receive only aggregate integrity booleans;
-- the database owner, which can already inspect event, retains diagnostic
-- access to the entry-level view.
revoke all on v_audit_chain_entry from public, carr_reader, carr_writer, carr_jobs, carr_exporter;
grant select on v_audit_chain_status to carr_reader, carr_writer, carr_exporter;

-- Put the verifier on the heartbeat surface. A chain nobody reads is an
-- integrity feature only on paper; this row makes any break part of the same
-- digest every session and /health already inspect.
create or replace view v_integrity_digest as
  select 'row_counts'::text as line,
    jsonb_build_object(
      'deals',         (select count(*) from deal),
      'clients',       (select count(*) from client),
      'leads',         (select count(*) from lead),
      'vendors',       (select count(*) from vendor),
      'activities_7d', (select count(*) from activity where recorded_at > now() - interval '7 days'),
      'events_24h',    (select count(*) from event where recorded_at > now() - interval '24 hours')
    ) as value
union all
  select 'writes_by_dell_24h'::text,
    to_jsonb((select count(*) from event e join actor a on a.id = e.actor_id
               where a.slug = 'dell' and e.recorded_at > now() - interval '24 hours'))
union all
  select 'export_freshness'::text,
    coalesce((
      select jsonb_object_agg(t.target, jsonb_build_object(
               'last_ok', t.last_ok,
               'stale', case when t.last_ok is null then null
                             else t.last_ok < now() - interval '26 hours' end,
               'state', case when t.last_ok is null then 'never_succeeded'
                             when t.last_ok < now() - interval '26 hours' then 'stale'
                             else 'fresh' end,
               'last_attempt', t.last_any,
               'last_attempt_status', t.last_status))
        from (select target,
                     max(ran_at) filter (where status='ok') as last_ok,
                     max(ran_at) as last_any,
                     (array_agg(status order by ran_at desc))[1] as last_status
                from export_run group by target) t), '{}'::jsonb)
union all
  select 'norm_owed_open'::text,
    to_jsonb((select count(*) from availability where norm_owed))
union all
  select 'merge_queue'::text,
    to_jsonb((select count(*) from ingest_inbox where status='new'))
union all
  select 'audit_chain'::text,
    coalesce((select jsonb_object_agg(organization_tenant_id,
      jsonb_build_object(
        'entries', entry_count,
        'chain_ok', chain_ok,
        'predecessor_breaks', predecessor_breaks,
        'payload_breaks', payload_breaks,
        'entry_hash_breaks', entry_hash_breaks,
        'metadata_breaks', metadata_breaks,
        'head_seq', head_seq,
        'checked_through', checked_through))
      from v_audit_chain_status), '{}'::jsonb);

-- Decision corrections are projections over immutable events. The base
-- log-decision row remains the historical act; the newest amendment supplies
-- the current readable fields.
create or replace view v_decision_entry as
select rs.external_key,
       split_part(rs.external_key, '#', 1) as source_file,
       split_part(rs.external_key, '#', 2) as session_key,
       e.occurred_at::date as entry_date,
       act.slug as author,
       current_value ->> 'title' as title,
       case when amendment.id is null then e.human_quote else amendment.human_quote end as human_quote,
       case when amendment.id is null then e.agent_rationale else amendment.agent_rationale end as agent_rationale,
       e.cause,
       (current_value ->> 'quote_absent')::boolean as quote_absent,
       current_value ->> 'provenance' as provenance,
       e.subject_id as decision_id,
       e.id as event_id,
       current_value ->> 'cost_delta' as cost_delta,
       current_value ->> 'quality_delta' as quality_delta,
       current_value ? 'cost_delta' as priced,
       e.occurred_at as occurred_at
  from record_source rs
  join event e on e.id = rs.entity_id and rs.entity_type = 'event'
  join actor act on act.id = e.actor_id
  left join lateral (
    select a.id, a.new_value, a.human_quote, a.agent_rationale
      from event a
     where a.subject_type='decision' and a.subject_id=e.subject_id
       and a.verb='amend-decision' and a.new_value ? 'current_new_value'
     order by a.recorded_at desc, a.id desc limit 1
  ) amendment on true
  cross join lateral (
    select coalesce(amendment.new_value->'current_new_value', e.new_value) as current_value
  ) projected
 where rs.source_system = 'decision-history';

comment on view v_decision_entry is
  'One row per logged decision. Since 0117 the original event is immutable and the newest '
  'amend-decision event supplies the current projection; event_id and occurred_at remain the original act.';

-- A detached decision pointer remains visible where it was first written, but
-- its display is derived from the later detach event instead of mutating the
-- pointer row in place.
create or replace view v_subject_timeline as
select 'activity' as entry_kind, a.id, a.occurred_at, a.recorded_at, act.slug as actor,
       a.kind as verb, a.summary, a.detail, a.owed,
       coalesce(a.deal_id, a.client_id, a.lead_id, a.vendor_id) as subject_id,
       case when a.deal_id is not null then 'deal'
            when a.client_id is not null then 'client'
            when a.lead_id is not null then 'lead'
            else 'vendor' end as subject_type
  from activity a join actor act on act.id = a.actor_id
union all
select 'event', e.id, e.occurred_at, e.recorded_at, act.slug,
       e.verb,
       case when detached.id is not null then
         'RETRACTED — not about this record (' ||
         coalesce(detached.new_value->>'retraction_reason', 'reason unavailable') ||
         ') — was: ' || coalesce(detached.new_value->>'summary_before_retraction',
                                  e.new_value->>'summary', e.field, e.verb)
       else coalesce(nullif(btrim(e.new_value->>'summary'), ''), e.field, e.verb)
       end,
       e.human_quote, null,
       e.subject_id, e.subject_type
  from event e
  join actor act on act.id = e.actor_id
  left join lateral (
    select d.id, d.new_value
      from event d
     where d.verb='detach-decision'
       and d.new_value->>'pointer_event_id'=e.id::text
     order by d.recorded_at desc, d.id desc limit 1
  ) detached on true;

commit;

do $$
declare
  v_update_grants int;
  v_trigger_count int;
begin
  select count(*) into v_update_grants
    from information_schema.role_table_grants
   where table_name in ('event','audit_ledger')
     and grantee in ('carr_writer','carr_jobs')
     and privilege_type in ('UPDATE','DELETE');
  if v_update_grants <> 0 then
    raise exception '0117 failed: append-only tables retain % writer UPDATE/DELETE grants', v_update_grants;
  end if;

  if has_table_privilege('carr_reader', 'audit_ledger', 'select')
     or has_table_privilege('carr_reader', 'v_audit_chain_entry', 'select') then
    raise exception '0117 failed: reader can inspect raw audit commitments';
  end if;
  if not has_table_privilege('carr_reader', 'v_audit_chain_status', 'select') then
    raise exception '0117 failed: reader cannot inspect aggregate audit status';
  end if;

  select count(*) into v_trigger_count
    from pg_trigger
   where tgrelid in ('event'::regclass, 'audit_ledger'::regclass)
     and not tgisinternal
     and tgname in ('event_append_audit_chain','event_append_only','audit_ledger_append_only');
  if v_trigger_count <> 3 then
    raise exception '0117 failed: expected 3 audit triggers, found %', v_trigger_count;
  end if;

  if not has_table_privilege('carr_reader', 'v_audit_chain_status', 'select') then
    raise exception '0117 failed: carr_reader cannot inspect chain status';
  end if;
end
$$;
