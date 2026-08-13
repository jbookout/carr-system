-- 0098_investigation_control_plane.sql
-- Durable, model-independent state for bounded analytical investigations.
-- Detection is deterministic, one actor owns judgment, worker contributions
-- are evidence packets, and every branch ends verified/rejected/pruned.

begin;

create table signal_event (
  id               uuid primary key default gen_random_uuid(),
  producer         text not null check (btrim(producer) <> ''),
  signal_key       text not null check (btrim(signal_key) <> ''),
  signal_kind      text not null check (btrim(signal_kind) <> ''),
  subject_type     text not null check (btrim(subject_type) <> ''),
  subject_ref      text not null check (btrim(subject_ref) <> ''),
  metric_name      text not null check (btrim(metric_name) <> ''),
  observed_value   numeric not null,
  baseline_value   numeric,
  threshold_value  numeric not null,
  comparison       text not null check (comparison in ('gt','gte','lt','lte','delta_abs_gte')),
  severity         text not null check (severity in ('info','warning','critical')),
  detected_at      timestamptz not null,
  evidence_refs    jsonb not null check (jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) > 0),
  payload          jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  status           text not null default 'open' check (status in ('open','claimed','resolved','dismissed')),
  created_by       uuid not null references actor(id),
  created_at       timestamptz not null default now(),
  unique (producer, signal_key)
);

comment on table signal_event is
  'Signals established by deterministic code before an LLM is asked to reason. '
  'The producer and signal_key make repeated scheduled detection idempotent across sessions.';

create index signal_event_queue_idx
  on signal_event (status, severity, detected_at desc);
create index signal_event_subject_idx
  on signal_event (subject_type, subject_ref, detected_at desc);

create table diagnostic_route (
  route_key        text primary key check (btrim(route_key) <> ''),
  signal_kind      text,
  from_kind        text not null check (btrim(from_kind) <> ''),
  relation         text not null check (btrim(relation) <> ''),
  to_kind          text not null check (btrim(to_kind) <> ''),
  test_verb        text not null check (btrim(test_verb) <> ''),
  input_contract   jsonb not null default '{}'::jsonb check (jsonb_typeof(input_contract) = 'object'),
  minimum_effect   numeric,
  active           boolean not null default true,
  created_by       uuid not null references actor(id),
  created_at       timestamptz not null default now()
);

comment on table diagnostic_route is
  'Allowlisted hypothesis edges. test_verb names a registered read capability; '
  'raw SQL is deliberately not stored or executed from this table.';

create index diagnostic_route_neighborhood_idx
  on diagnostic_route (from_kind, signal_kind) where active;

create table investigation_run (
  id                    uuid primary key default gen_random_uuid(),
  signal_id             uuid not null references signal_event(id),
  objective             text not null check (btrim(objective) <> ''),
  owner_actor_id        uuid not null references actor(id),
  max_depth             integer not null default 3 check (max_depth between 1 and 6),
  status                text not null default 'open' check (status in ('open','completed','abandoned')),
  conclusion            text,
  confidence            numeric check (confidence between 0 and 1),
  strongest_alternative text,
  alternative_disposition text,
  termination_reason    text check (termination_reason in
                          ('root_cause_found','budget_exhausted','insufficient_evidence','signal_invalid','superseded')),
  opened_at             timestamptz not null default now(),
  closed_at             timestamptz,
  check ((status = 'open' and closed_at is null)
      or (status <> 'open' and closed_at is not null))
);

create unique index investigation_one_open_per_signal
  on investigation_run (signal_id) where status = 'open';

create table investigation_branch (
  id               uuid primary key default gen_random_uuid(),
  run_id           uuid not null references investigation_run(id),
  parent_branch_id uuid references investigation_branch(id),
  route_key        text not null references diagnostic_route(route_key),
  depth            integer not null check (depth between 1 and 6),
  hypothesis       text not null check (btrim(hypothesis) <> ''),
  test_input       jsonb not null default '{}'::jsonb check (jsonb_typeof(test_input) = 'object'),
  status           text not null default 'open' check (status in ('open','verified','rejected','pruned','inconclusive')),
  effect_size      numeric,
  adjudication     text,
  adjudicated_by   uuid references actor(id),
  adjudicated_at   timestamptz,
  opened_by        uuid not null references actor(id),
  opened_at        timestamptz not null default now(),
  check ((status = 'open' and adjudicated_at is null and adjudicated_by is null)
      or (status <> 'open' and adjudicated_at is not null and adjudicated_by is not null))
);

create index investigation_branch_run_idx
  on investigation_branch (run_id, depth, opened_at);

create table investigation_evidence (
  id               uuid primary key default gen_random_uuid(),
  branch_id        uuid not null references investigation_branch(id),
  contributor_id   uuid not null references actor(id),
  scope            text not null check (btrim(scope) <> ''),
  query_or_tool    text not null check (btrim(query_or_tool) <> ''),
  raw_facts        jsonb not null check (jsonb_typeof(raw_facts) = 'array'),
  evidence_refs    jsonb not null check (jsonb_typeof(evidence_refs) = 'array'),
  uncertainty      text,
  nothing_found    boolean not null default false,
  exclusions       jsonb not null default '[]'::jsonb check (jsonb_typeof(exclusions) = 'array'),
  recorded_at      timestamptz not null default now(),
  check (nothing_found or jsonb_array_length(evidence_refs) > 0),
  check (nothing_found or jsonb_array_length(raw_facts) > 0)
);

comment on table investigation_evidence is
  'Worker return packets: scoped raw facts and evidence only. Global recommendations '
  'are absent by schema so the investigation owner retains coherent judgment.';

create index investigation_evidence_branch_idx
  on investigation_evidence (branch_id, recorded_at);

create view v_signal_queue as
select s.id, s.producer, s.signal_key, s.signal_kind, s.subject_type, s.subject_ref,
       s.metric_name, s.observed_value, s.baseline_value, s.threshold_value,
       s.comparison, s.severity, s.detected_at, s.evidence_refs, s.payload, s.status,
       a.slug as created_by
  from signal_event s join actor a on a.id = s.created_by;

create view v_investigation as
select r.id, r.signal_id, s.signal_kind, s.subject_type, s.subject_ref,
       r.objective, a.slug as owner, r.max_depth, r.status, r.conclusion,
       r.confidence, r.strongest_alternative, r.alternative_disposition,
       r.termination_reason, r.opened_at, r.closed_at,
       count(b.id) as branch_count,
       count(b.id) filter (where b.status = 'open') as open_branch_count
  from investigation_run r
  join signal_event s on s.id = r.signal_id
  join actor a on a.id = r.owner_actor_id
  left join investigation_branch b on b.run_id = r.id
 group by r.id, s.id, a.slug;

grant select, insert, update on signal_event, investigation_run,
  investigation_branch, investigation_evidence to carr_writer;
grant select on diagnostic_route to carr_writer;
grant select on v_signal_queue, v_investigation, diagnostic_route to carr_reader;
grant select on v_signal_queue, v_investigation to carr_writer;

commit;
