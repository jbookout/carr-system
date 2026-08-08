-- 0081_capture_bridge.sql -- consent-gated capture sessions and the human
-- confirmation queue. DDL only; deployment is owned by orchestration.

begin;

create table capture_session (
  id                    uuid primary key default gen_random_uuid(),
  nonce                 text not null unique,
  device_id             text not null,
  actor_id              uuid not null references actor(id),
  mode                  text not null check (mode = 'meeting'),
  started_at            timestamptz not null,
  consent_announced_at  timestamptz not null,
  session_token_hash    text not null unique,
  expires_at            timestamptz not null,
  state                 text not null default 'recording'
                        check (state in ('recording','transcribing','distilling','done','failed')),
  state_at              timestamptz not null,
  state_detail          text,
  created_at            timestamptz not null default now()
);
create index capture_session_active_idx on capture_session (expires_at, state);

create table capture_candidate (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid not null references capture_session(id),
  idempotency_key  text not null,
  batch_hash       text not null,
  item_index       integer not null check (item_index >= 0),
  kind             text not null check
                   (kind in ('phase_move','next_step','new_deal','activity','meeting_record')),
  payload          jsonb not null check (jsonb_typeof(payload) = 'object'),
  evidence_quote   text not null check
                   (array_length(regexp_split_to_array(btrim(evidence_quote), '\s+'), 1) <= 15),
  confidence       numeric not null check (confidence >= 0 and confidence <= 1),
  status           text not null default 'pending'
                   check (status in ('pending','confirmed','skipped')),
  resolved_by      uuid references actor(id),
  resolution_note  text,
  resulting_ref    text,
  created_at       timestamptz not null default now(),
  resolved_at      timestamptz,
  unique (session_id, idempotency_key, item_index),
  check ((status = 'pending' and resolved_by is null and resolved_at is null and resulting_ref is null)
      or (status = 'skipped' and resolved_by is not null and resolved_at is not null and resulting_ref is null)
      or (status = 'confirmed' and resolved_by is not null and resolved_at is not null and resulting_ref is not null))
);
create index capture_candidate_queue_idx
  on capture_candidate (session_id, status, confidence desc, created_at, id);

create view v_capture_session_status as
select s.id as session_id, s.device_id, s.state, s.started_at, s.state_at
  from capture_session s
 where s.expires_at > now();

create view v_capture_candidate_queue as
select c.id, c.session_id, c.kind, c.payload, c.evidence_quote, c.confidence,
       c.created_at,
       coalesce(direct_deal.name, named_deal.name) as deal_name
  from capture_candidate c
  join capture_session s on s.id = c.session_id
  left join deal direct_deal
    on direct_deal.id::text = coalesce(c.payload->>'deal', c.payload->>'ref')
  left join lateral (
    select d.name
      from deal d
     where d.name ilike coalesce(c.payload->>'deal', c.payload->>'ref')
     order by d.name
     limit 1
  ) named_deal on direct_deal.id is null
 where c.status = 'pending' and s.expires_at > now();

grant select, insert, update on capture_session, capture_candidate to carr_writer;
grant select on v_capture_session_status, v_capture_candidate_queue to carr_reader;

commit;
