-- 0116_investigation_method_ledger.sql
-- Resumable, model-independent method ledger for 0098 investigations.  The
-- registries declare the admissible input surfaces and matchers; inventories,
-- waves, assessments, checkpoints, and releases preserve what actually ran.
-- No prompt, output, or candidate evidence is overwritten in place.

begin;

-- A surface is a stable address for an inventory source, not executable work.
create table investigation_surface (
  id                         uuid not null default gen_random_uuid() unique,
  surface_key                text primary key check (btrim(surface_key) <> ''),
  title                      text not null check (btrim(title) <> ''),
  source_kind                text not null check (btrim(source_kind) <> ''),
  source_ref                 text not null check (btrim(source_ref) <> ''),
  inventory_contract_version text not null check (btrim(inventory_contract_version) <> ''),
  candidate_key_version      text not null check (btrim(candidate_key_version) <> ''),
  release_routes             jsonb not null default '["record-finding"]'::jsonb
                             check (release_routes = '["record-finding"]'::jsonb),
  created_by                 uuid not null references actor(id),
  created_at                 timestamptz not null default now()
);

comment on table investigation_surface is
  'Registry of stable inventory surfaces for an investigation method. source_ref and '
  'release_routes are declarative addresses only; this table never stores SQL or executable code.';

create table investigation_matcher (
  id          uuid primary key default gen_random_uuid(),
  surface_key text not null references investigation_surface(surface_key),
  matcher_key text not null check (btrim(matcher_key) <> ''),
  version     text not null check (btrim(version) <> ''),
  matcher_type text not null check (matcher_type in ('regex','path','metadata')),
  spec        jsonb not null check (jsonb_typeof(spec) = 'object'),
  examples    jsonb not null default '[]'::jsonb check (jsonb_typeof(examples) = 'array'),
  created_by  uuid not null references actor(id),
  created_at  timestamptz not null default now(),
  unique (surface_key, matcher_key, version)
);

comment on table investigation_matcher is
  'Versioned declarative candidate matcher. Verbs validate matcher safety and examples; '
  'the database stores no executable matcher SQL or code.';

create index investigation_matcher_surface_idx
  on investigation_matcher (surface_key, matcher_key, version);

-- Budget/coverage policy is recorded once per run, before work begins.
create table investigation_method_policy (
  run_id                           uuid primary key references investigation_run(id),
  required_surfaces               jsonb not null
                                  check (jsonb_typeof(required_surfaces) = 'array'
                                     and jsonb_array_length(required_surfaces) > 0),
  min_coverage_ratio               numeric not null default 1
                                   check (min_coverage_ratio >= 0 and min_coverage_ratio <= 1),
  require_sensitive_coverage       boolean not null default false,
  require_representative_coverage  boolean not null default false,
  max_cost_amount                  numeric check (max_cost_amount is null or max_cost_amount >= 0),
  cost_currency                    text not null default 'USD'
                                   check (cost_currency ~ '^[A-Z]{3}$'),
  max_duration_seconds             integer check (max_duration_seconds is null or max_duration_seconds > 0),
  created_by                       uuid not null references actor(id),
  created_at                       timestamptz not null default now()
);

comment on table investigation_method_policy is
  'Per-run coverage, cost, and duration budget. A new policy means a new investigation run; '
  'this row is intentionally insert-only so a completed method remains auditable.';

create table investigation_wave_reservation (
  id                      uuid primary key default gen_random_uuid(),
  run_id                  uuid not null references investigation_run(id),
  phase                   text not null check (phase in ('inventory','primary_assessment','revalidation','release')),
  role                    text not null check (role in ('primary','revalidation')),
  actor_id                uuid not null references actor(id),
  context_key             text not null check (btrim(context_key) <> ''),
  requested_provider      text not null check (btrim(requested_provider) <> ''),
  requested_model         text not null check (btrim(requested_model) <> ''),
  requested_effort        text,
  prompt_contract_version text not null check (btrim(prompt_contract_version) <> ''),
  prompt_sha256           text not null check (prompt_sha256 ~ '^[0-9a-f]{64}$'),
  input_digest            text not null check (input_digest ~ '^[0-9a-f]{64}$'),
  reserved_cost_amount    numeric not null check (reserved_cost_amount >= 0),
  cost_currency           text not null check (cost_currency ~ '^[A-Z]{3}$'),
  reserved_duration_seconds integer not null check (reserved_duration_seconds > 0),
  source_checkpoint_id    uuid,
  expires_at              timestamptz not null,
  created_at              timestamptz not null default now(),
  unique (run_id, context_key),
  check (expires_at > created_at),
  check ((role = 'primary' and phase in ('inventory','primary_assessment','release')
          and source_checkpoint_id is null)
      or (role = 'revalidation' and phase = 'revalidation' and source_checkpoint_id is not null))
);

comment on table investigation_wave_reservation is
  'Append-only pre-spend reservation. Estimated cost and duration consume the run budget '
  'before an external model is called; expired unused reservations stop consuming it.';

create table investigation_wave (
  id                      uuid primary key default gen_random_uuid(),
  reservation_id          uuid not null unique references investigation_wave_reservation(id),
  run_id                  uuid not null references investigation_run(id),
  phase                   text not null check (phase in
                          ('inventory','primary_assessment','revalidation','release')),
  role                    text not null check (role in ('primary','revalidation')),
  actor_id                uuid not null references actor(id),
  context_key             text not null check (btrim(context_key) <> ''),
  requested_provider      text not null check (btrim(requested_provider) <> ''),
  requested_model         text not null check (btrim(requested_model) <> ''),
  requested_effort        text,
  prompt_contract_version text not null check (btrim(prompt_contract_version) <> ''),
  prompt_sha256           text not null check (prompt_sha256 ~ '^[0-9a-f]{64}$'),
  input_digest            text not null check (input_digest ~ '^[0-9a-f]{64}$'),
  output_digest           text not null check (output_digest ~ '^[0-9a-f]{64}$'),
  started_at              timestamptz not null default now(),
  finished_at             timestamptz not null,
  input_tokens            bigint check (input_tokens is null or input_tokens >= 0),
  output_tokens           bigint check (output_tokens is null or output_tokens >= 0),
  cost_amount             numeric check (cost_amount is null or cost_amount >= 0),
  cost_currency           text check (cost_currency is null or cost_currency ~ '^[A-Z]{3}$'),
  cost_source             text,
  created_at              timestamptz not null default now(),
  unique (id, run_id),
  unique (run_id, context_key),
  check (finished_at >= started_at),
  check ((input_tokens is null and output_tokens is null)
      or (input_tokens is not null and output_tokens is not null)),
  check ((cost_amount is null and cost_currency is null and cost_source is null)
      or (cost_amount is not null and cost_currency is not null and btrim(cost_source) <> '')),
  check ((role = 'primary' and phase in ('inventory','primary_assessment','release'))
      or (role = 'revalidation' and phase = 'revalidation'))
);

comment on table investigation_wave is
  'Append-only execution receipt. context_key identifies a fresh working context, so a '
  'revalidation is independently attributable even when it uses the same actor or model.';

create index investigation_wave_run_phase_idx
  on investigation_wave (run_id, phase, started_at, id);
create index investigation_wave_context_idx
  on investigation_wave (run_id, context_key, started_at, id);

-- One inventory row exists even when a surface yielded no candidates.
create table investigation_inventory (
  run_id            uuid not null references investigation_run(id),
  surface_key       text not null references investigation_surface(surface_key),
  item_count        integer not null check (item_count >= 0),
  scanned_count     integer not null check (scanned_count between 0 and item_count),
  matcher_refs      jsonb not null default '[]'::jsonb check (jsonb_typeof(matcher_refs) = 'array'),
  evidence_refs     jsonb not null check (jsonb_typeof(evidence_refs) = 'array'
                       and jsonb_array_length(evidence_refs) > 0),
  input_digest      text not null check (input_digest ~ '^[0-9a-f]{64}$'),
  batch_digest      text not null check (batch_digest ~ '^[0-9a-f]{64}$'),
  declared_inventory_count integer not null check (declared_inventory_count >= 1),
  declared_candidate_count integer not null check (declared_candidate_count >= 0),
  representative    boolean not null default false,
  sensitive         boolean not null default false,
  inventory_wave_id uuid not null,
  created_by        uuid not null references actor(id),
  created_at        timestamptz not null default now(),
  primary key (run_id, surface_key),
  foreign key (inventory_wave_id, run_id) references investigation_wave(id, run_id)
);

comment on table investigation_inventory is
  'Append-only per-run surface inventory, including zero-candidate evidence. A candidate '
  'cannot exist before its surface inventory, so absence is distinguishable from not attempted.';

create index investigation_inventory_wave_idx on investigation_inventory (inventory_wave_id);
create index investigation_inventory_run_coverage_idx
  on investigation_inventory (run_id, representative, sensitive, scanned_count, item_count);

create table investigation_candidate (
  id                uuid primary key default gen_random_uuid(),
  run_id            uuid not null references investigation_run(id),
  surface_key       text not null,
  candidate_key     text not null check (btrim(candidate_key) <> ''),
  ordinal           integer not null check (ordinal >= 1),
  subject_type      text not null check (btrim(subject_type) <> ''),
  subject_id        uuid not null,
  evidence_refs     jsonb not null check (jsonb_typeof(evidence_refs) = 'array'
                    and jsonb_array_length(evidence_refs) > 0),
  input_digest      text not null check (input_digest ~ '^[0-9a-f]{64}$'),
  inventory_wave_id uuid not null,
  matcher_key       text not null check (btrim(matcher_key) <> ''),
  matcher_version   text not null check (btrim(matcher_version) <> ''),
  created_by        uuid not null references actor(id),
  created_at        timestamptz not null default now(),
  unique (run_id, surface_key, candidate_key),
  unique (run_id, surface_key, ordinal),
  unique (run_id, ordinal),
  foreign key (run_id, surface_key) references investigation_inventory(run_id, surface_key),
  foreign key (inventory_wave_id, run_id) references investigation_wave(id, run_id),
  foreign key (surface_key, matcher_key, matcher_version)
    references investigation_matcher(surface_key, matcher_key, version)
);

comment on table investigation_candidate is
  'Append-only stable, evidence-backed candidate address within one run and surface. '
  'Candidate keys deduplicate reruns; ordinal preserves the original deterministic inventory order.';

create index investigation_candidate_run_surface_idx
  on investigation_candidate (run_id, surface_key, ordinal, id);
create index investigation_candidate_inventory_wave_idx
  on investigation_candidate (inventory_wave_id, ordinal, id);

create table investigation_candidate_assessment (
  id            uuid primary key default gen_random_uuid(),
  candidate_id  uuid not null references investigation_candidate(id),
  wave_id       uuid not null references investigation_wave(id),
  role          text not null check (role in ('primary','revalidation')),
  outcome       text not null check (outcome in
                ('pending','validated','rejected','error','skipped','refused')),
  evidence_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_refs) = 'array'),
  result_digest text check (result_digest is null or result_digest ~ '^[0-9a-f]{64}$'),
  reason        text,
  actor_id      uuid not null references actor(id),
  recorded_at   timestamptz not null default now(),
  unique (candidate_id, wave_id, role),
  check ((outcome = 'pending' and result_digest is null)
      or (outcome <> 'pending' and result_digest is not null)),
  check (outcome = 'pending' or btrim(coalesce(reason, '')) <> '')
);

comment on table investigation_candidate_assessment is
  'Append-only outcome receipt. A retry or revalidation adds a row rather than changing '
  'a prior result, preserving pending, refused, error, and negative evidence.';

create index investigation_candidate_assessment_latest_idx
  on investigation_candidate_assessment (candidate_id, role, recorded_at desc, id desc);
create index investigation_candidate_assessment_wave_idx
  on investigation_candidate_assessment (wave_id, outcome, recorded_at, id);

create table investigation_phase_checkpoint (
  id                     uuid primary key default gen_random_uuid(),
  run_id                 uuid not null references investigation_run(id),
  phase                  text not null check (phase in
                         ('inventory','primary_assessment','revalidation','release')),
  source_checkpoint_id   uuid references investigation_phase_checkpoint(id),
  sequence               integer not null check (sequence >= 1),
  candidate_set_digest   text not null check (candidate_set_digest ~ '^[0-9a-f]{64}$'),
  assessment_set_digest  text not null check (assessment_set_digest ~ '^[0-9a-f]{64}$'),
  total_count            integer not null check (total_count >= 0),
  unattempted_count      integer not null check (unattempted_count >= 0),
  pending_count          integer not null check (pending_count >= 0),
  validated_count        integer not null check (validated_count >= 0),
  rejected_count         integer not null check (rejected_count >= 0),
  error_count            integer not null check (error_count >= 0),
  skipped_count          integer not null check (skipped_count >= 0),
  refused_count          integer not null check (refused_count >= 0),
  verdict                text not null check (verdict in ('complete','degraded','blocked')),
  created_by             uuid not null references actor(id),
  created_at             timestamptz not null default now(),
  unique (run_id, phase, sequence),
  unique (id, run_id),
  check (total_count = unattempted_count + pending_count + validated_count + rejected_count
                     + error_count + skipped_count + refused_count),
  check ((verdict = 'blocked' and (unattempted_count > 0 or pending_count > 0))
      or (verdict = 'degraded' and unattempted_count = 0 and pending_count = 0
          and (error_count > 0 or skipped_count > 0 or refused_count > 0))
      or (verdict = 'complete' and unattempted_count = 0 and pending_count = 0
          and error_count = 0 and skipped_count = 0 and refused_count = 0))
  ,check ((phase = 'revalidation' and source_checkpoint_id is not null)
       or (phase <> 'revalidation' and source_checkpoint_id is null))
);

comment on table investigation_phase_checkpoint is
  'Append-only exact count and digest checkpoint. unattempted is explicit so a zero '
  'validated count never masquerades as complete coverage.';

create index investigation_phase_checkpoint_latest_idx
  on investigation_phase_checkpoint (run_id, phase, sequence desc, created_at desc, id desc);

alter table investigation_wave_reservation
  add constraint investigation_wave_reservation_source_checkpoint_fkey
  foreign key (source_checkpoint_id) references investigation_phase_checkpoint(id);

create table investigation_checkpoint_candidate (
  checkpoint_id uuid not null references investigation_phase_checkpoint(id),
  candidate_id  uuid not null references investigation_candidate(id),
  assessment_id uuid references investigation_candidate_assessment(id),
  outcome       text not null check (outcome in
                ('unattempted','pending','validated','rejected','error','skipped','refused')),
  primary key (checkpoint_id, candidate_id)
);

comment on table investigation_checkpoint_candidate is
  'Immutable checkpoint membership. It pins which assessment outcome each candidate had '
  'when the aggregate digest and counts were created.';

create table investigation_candidate_release (
  id            uuid primary key default gen_random_uuid(),
  candidate_id  uuid not null references investigation_candidate(id),
  checkpoint_id uuid not null references investigation_phase_checkpoint(id),
  route_key     text not null check (route_key = 'record-finding'),
  finding_id    uuid not null references record_flag(id),
  created_by    uuid not null references actor(id),
  created_at    timestamptz not null default now(),
  unique (candidate_id, checkpoint_id),
  unique (finding_id)
);

comment on table investigation_candidate_release is
  'Append-only promotion receipt. Initial routing is deliberately restricted to '
  'record-finding; a broader release path requires a later explicit migration.';

create index investigation_candidate_release_checkpoint_idx
  on investigation_candidate_release (checkpoint_id, created_at, id);
create index investigation_candidate_release_finding_idx
  on investigation_candidate_release (finding_id) where finding_id is not null;

-- Cross-row invariants which cannot be expressed as ordinary foreign keys.
create function trg_investigation_inventory_wave_integrity()
returns trigger language plpgsql as $$
declare
  v_phase text;
begin
  select phase into v_phase from investigation_wave
   where id = new.inventory_wave_id and run_id = new.run_id;
  if v_phase is distinct from 'inventory' then
    raise exception 'investigation_inventory requires an inventory wave for the same run';
  end if;
  return new;
end;
$$;

create function trg_investigation_candidate_wave_integrity()
returns trigger language plpgsql as $$
declare
  v_phase text;
begin
  select phase into v_phase from investigation_wave
   where id = new.inventory_wave_id and run_id = new.run_id;
  if v_phase is distinct from 'inventory' then
    raise exception 'investigation_candidate requires an inventory wave for the same run';
  end if;
  return new;
end;
$$;

create function trg_investigation_assessment_integrity()
returns trigger language plpgsql as $$
declare
  v_candidate_run uuid;
  v_wave_run uuid;
  v_phase  text;
  v_role   text;
  v_wave_actor uuid;
  v_primary_actor uuid;
begin
  select c.run_id, w.run_id, w.phase, w.role, w.actor_id
    into v_candidate_run, v_wave_run, v_phase, v_role, v_wave_actor
    from investigation_candidate c
    join investigation_wave w on w.id = new.wave_id
   where c.id = new.candidate_id;
  if v_candidate_run is null or v_candidate_run is distinct from v_wave_run then
    raise exception 'investigation assessment candidate and wave must belong to the same run';
  end if;
  if v_role <> new.role
     or (new.role = 'primary' and v_phase <> 'primary_assessment')
     or (new.role = 'revalidation' and v_phase <> 'revalidation') then
    raise exception 'investigation assessment role must match its assessment wave';
  end if;
  if v_wave_actor is distinct from new.actor_id then
    raise exception 'investigation assessment actor must match its wave actor';
  end if;
  if new.role = 'revalidation' then
    select actor_id into v_primary_actor
      from investigation_candidate_assessment
     where candidate_id=new.candidate_id and role='primary'
     order by recorded_at desc,id desc limit 1;
    if v_primary_actor is null or v_primary_actor = new.actor_id then
      raise exception 'revalidation requires a different server-derived actor';
    end if;
  end if;
  return new;
end;
$$;

create function trg_investigation_release_integrity()
returns trigger language plpgsql as $$
declare
  v_candidate_run uuid;
  v_checkpoint_run uuid;
  v_checkpoint_phase text;
  v_checkpoint_verdict text;
  v_source_checkpoint uuid;
  v_primary_outcome text;
  v_revalidation_outcome text;
  v_primary_actor uuid;
  v_revalidation_actor uuid;
begin
  select run_id into v_candidate_run from investigation_candidate where id = new.candidate_id;
  select run_id, phase, verdict, source_checkpoint_id
    into v_checkpoint_run, v_checkpoint_phase, v_checkpoint_verdict, v_source_checkpoint
    from investigation_phase_checkpoint where id = new.checkpoint_id;
  if v_candidate_run is distinct from v_checkpoint_run then
    raise exception 'investigation release candidate and checkpoint must belong to the same run';
  end if;
  if v_checkpoint_phase <> 'revalidation' or v_checkpoint_verdict = 'blocked' then
    raise exception 'investigation release requires a non-blocked revalidation checkpoint';
  end if;
  if exists (select 1 from investigation_phase_checkpoint newer
              where newer.run_id=v_checkpoint_run and newer.phase='revalidation'
                and newer.sequence>(select sequence from investigation_phase_checkpoint where id=new.checkpoint_id))
     or exists (select 1 from investigation_phase_checkpoint newer
                 where newer.run_id=v_checkpoint_run and newer.phase='primary_assessment'
                   and newer.sequence>(select sequence from investigation_phase_checkpoint where id=v_source_checkpoint)) then
    raise exception 'investigation release requires the latest pinned checkpoints';
  end if;
  select cc.outcome, a.actor_id into v_primary_outcome, v_primary_actor
    from investigation_checkpoint_candidate cc
    left join investigation_candidate_assessment a on a.id=cc.assessment_id
   where cc.checkpoint_id=v_source_checkpoint and cc.candidate_id=new.candidate_id;
  select cc.outcome, a.actor_id into v_revalidation_outcome, v_revalidation_actor
    from investigation_checkpoint_candidate cc
    left join investigation_candidate_assessment a on a.id=cc.assessment_id
   where cc.checkpoint_id=new.checkpoint_id and cc.candidate_id=new.candidate_id;
  if v_primary_outcome is distinct from 'validated'
     or v_revalidation_outcome is distinct from 'validated' then
    raise exception 'investigation release requires current primary and revalidation outcomes to be validated';
  end if;
  if v_primary_actor is not distinct from v_revalidation_actor then
    raise exception 'investigation release requires a different revalidation actor';
  end if;
  return new;
end;
$$;

create function trg_investigation_reservation_budget()
returns trigger language plpgsql as $$
declare
  v_policy investigation_method_policy%rowtype;
  v_cost numeric;
  v_seconds numeric;
  v_checkpoint record;
begin
  select * into v_policy from investigation_method_policy
   where run_id = new.run_id for update;
  if not found then
    raise exception 'investigation wave requires a method policy for the run';
  end if;
  if new.role='revalidation' then
    select run_id,phase,verdict into v_checkpoint from investigation_phase_checkpoint
     where id=new.source_checkpoint_id;
    if not found or v_checkpoint.run_id<>new.run_id
       or v_checkpoint.phase<>'primary_assessment' or v_checkpoint.verdict='blocked' then
      raise exception 'revalidation reservation requires a non-blocked primary checkpoint for the same run';
    end if;
  end if;
  if v_policy.max_cost_amount is not null then
    if new.cost_currency <> v_policy.cost_currency then
      raise exception 'investigation reservation currency must match policy currency';
    end if;
    select coalesce((select sum(w.cost_amount) from investigation_wave w where w.run_id=new.run_id),0)
         + coalesce((select sum(r.reserved_cost_amount) from investigation_wave_reservation r
              left join investigation_wave w on w.reservation_id=r.id
             where r.run_id=new.run_id and w.id is null and r.expires_at>now()),0)
         + new.reserved_cost_amount into v_cost;
    if v_cost > v_policy.max_cost_amount then
      raise exception 'investigation cost budget exceeded';
    end if;
  end if;
  if v_policy.max_duration_seconds is not null then
    select coalesce((select sum(extract(epoch from (w.finished_at-w.started_at)))
                       from investigation_wave w where w.run_id=new.run_id),0)
         + coalesce((select sum(r.reserved_duration_seconds) from investigation_wave_reservation r
              left join investigation_wave w on w.reservation_id=r.id
             where r.run_id=new.run_id and w.id is null and r.expires_at>now()),0)
         + new.reserved_duration_seconds into v_seconds;
    if v_seconds > v_policy.max_duration_seconds then
      raise exception 'investigation duration budget exceeded';
    end if;
  end if;
  return new;
end;
$$;

create function trg_investigation_wave_reservation_integrity()
returns trigger language plpgsql as $$
declare
  r investigation_wave_reservation%rowtype;
  p investigation_method_policy%rowtype;
begin
  select * into r from investigation_wave_reservation where id=new.reservation_id for update;
  if not found then
    raise exception 'investigation wave requires a reservation';
  end if;
  if r.run_id<>new.run_id or r.phase<>new.phase or r.role<>new.role or r.actor_id<>new.actor_id
     or r.context_key<>new.context_key or r.requested_provider<>new.requested_provider
     or r.requested_model<>new.requested_model
     or r.requested_effort is distinct from new.requested_effort
     or r.prompt_contract_version<>new.prompt_contract_version
     or r.prompt_sha256<>new.prompt_sha256 or r.input_digest<>new.input_digest then
    raise exception 'investigation wave does not match its reservation';
  end if;
  select * into p from investigation_method_policy where run_id=new.run_id;
  if p.max_cost_amount is not null
     and (new.cost_amount is null or new.cost_currency<>p.cost_currency
          or btrim(coalesce(new.cost_source,''))='') then
    raise exception 'budgeted completed wave requires measured cost in policy currency';
  end if;
  return new;
end;
$$;

create function trg_investigation_refuse_append_mutation()
returns trigger language plpgsql as $$
begin
  raise exception '% is append-only; % is not permitted', tg_table_name, tg_op
    using errcode = '55000';
end;
$$;

create trigger investigation_inventory_wave_integrity
  before insert on investigation_inventory
  for each row execute function trg_investigation_inventory_wave_integrity();
create trigger investigation_candidate_wave_integrity
  before insert on investigation_candidate
  for each row execute function trg_investigation_candidate_wave_integrity();
create trigger investigation_candidate_assessment_integrity
  before insert on investigation_candidate_assessment
  for each row execute function trg_investigation_assessment_integrity();
create trigger investigation_candidate_release_integrity
  before insert on investigation_candidate_release
  for each row execute function trg_investigation_release_integrity();
create trigger investigation_reservation_budget
  before insert on investigation_wave_reservation
  for each row execute function trg_investigation_reservation_budget();
create trigger investigation_wave_reservation_integrity
  before insert on investigation_wave
  for each row execute function trg_investigation_wave_reservation_integrity();

create trigger investigation_inventory_append_only
  before update or delete on investigation_inventory
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_wave_append_only
  before update or delete on investigation_wave
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_wave_reservation_append_only
  before update or delete on investigation_wave_reservation
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_candidate_append_only
  before update or delete on investigation_candidate
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_candidate_assessment_append_only
  before update or delete on investigation_candidate_assessment
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_phase_checkpoint_append_only
  before update or delete on investigation_phase_checkpoint
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_checkpoint_candidate_append_only
  before update or delete on investigation_checkpoint_candidate
  for each row execute function trg_investigation_refuse_append_mutation();
create trigger investigation_candidate_release_append_only
  before update or delete on investigation_candidate_release
  for each row execute function trg_investigation_refuse_append_mutation();

create view v_investigation_candidate_latest_assessment as
select distinct on (a.candidate_id, a.role)
       a.candidate_id, c.run_id, c.surface_key, c.candidate_key, c.ordinal,
       c.subject_type, c.subject_id, a.role, a.outcome, a.evidence_refs,
       a.result_digest, a.reason, actor.slug as actor, a.wave_id, a.recorded_at
  from investigation_candidate_assessment a
  join investigation_candidate c on c.id = a.candidate_id
  join actor on actor.id = a.actor_id
 order by a.candidate_id, a.role, a.recorded_at desc, a.id desc;

comment on view v_investigation_candidate_latest_assessment is
  'Latest append-only assessment for each candidate and assessment role; primary and '
  'revalidation remain independently visible.';

create view v_investigation_coverage as
with candidate_counts as (
  select run_id, surface_key, count(*)::integer as candidate_count
    from investigation_candidate
   group by run_id, surface_key
), latest_checkpoint as (
  select distinct on (run_id, phase)
         run_id, phase, sequence, total_count, unattempted_count, pending_count,
         validated_count, rejected_count, error_count, skipped_count, refused_count,
         verdict, created_at
    from investigation_phase_checkpoint
   order by run_id, phase, sequence desc, created_at desc, id desc
)
select i.run_id, i.surface_key, s.title as surface_title, i.item_count, i.scanned_count,
       case when i.item_count = 0 then 1::numeric
            else i.scanned_count::numeric / i.item_count end as coverage_ratio,
       i.representative, i.sensitive, i.matcher_refs, i.evidence_refs, i.input_digest,
       i.batch_digest, i.declared_inventory_count, i.declared_candidate_count,
       i.inventory_wave_id, coalesce(c.candidate_count, 0) as candidate_count,
       mp.required_surfaces, mp.min_coverage_ratio, mp.require_sensitive_coverage,
       mp.require_representative_coverage, mp.max_cost_amount, mp.cost_currency,
       mp.max_duration_seconds,
       p.sequence as primary_checkpoint_sequence, p.total_count as primary_total_count,
       p.unattempted_count as primary_unattempted_count,
       p.pending_count as primary_pending_count,
       p.validated_count as primary_validated_count,
       p.rejected_count as primary_rejected_count, p.error_count as primary_error_count,
       p.skipped_count as primary_skipped_count, p.refused_count as primary_refused_count,
       p.verdict as primary_verdict, p.created_at as primary_checkpoint_at
  from investigation_inventory i
  join investigation_surface s on s.surface_key = i.surface_key
  left join investigation_method_policy mp on mp.run_id = i.run_id
  left join candidate_counts c on c.run_id = i.run_id and c.surface_key = i.surface_key
  left join latest_checkpoint p on p.run_id = i.run_id and p.phase = 'primary_assessment';

comment on view v_investigation_coverage is
  'Coverage surface for resumable investigations: every inventory, including zero candidates, '
  'with current primary assessment checkpoint counts and verdict.';

grant select, insert on investigation_surface, investigation_matcher to carr_writer;
grant select, insert on investigation_method_policy, investigation_inventory,
  investigation_candidate, investigation_wave_reservation, investigation_wave,
  investigation_candidate_assessment, investigation_phase_checkpoint,
  investigation_checkpoint_candidate, investigation_candidate_release to carr_writer;
grant select on investigation_surface, investigation_matcher, investigation_method_policy,
  investigation_inventory, investigation_candidate, investigation_wave_reservation,
  investigation_wave, investigation_candidate_assessment, investigation_phase_checkpoint,
  investigation_checkpoint_candidate, investigation_candidate_release to carr_reader;
grant select on v_investigation_candidate_latest_assessment, v_investigation_coverage
  to carr_reader, carr_writer;

revoke all on function trg_investigation_inventory_wave_integrity() from public;
revoke all on function trg_investigation_candidate_wave_integrity() from public;
revoke all on function trg_investigation_assessment_integrity() from public;
revoke all on function trg_investigation_release_integrity() from public;
revoke all on function trg_investigation_reservation_budget() from public;
revoke all on function trg_investigation_wave_reservation_integrity() from public;
revoke all on function trg_investigation_refuse_append_mutation() from public;
grant execute on function trg_investigation_inventory_wave_integrity(),
  trg_investigation_candidate_wave_integrity(), trg_investigation_assessment_integrity(),
  trg_investigation_release_integrity(), trg_investigation_reservation_budget(),
  trg_investigation_wave_reservation_integrity(),
  trg_investigation_refuse_append_mutation() to carr_writer;

do $$
declare
  v_table text;
begin
  foreach v_table in array array[
    'investigation_inventory', 'investigation_wave_reservation', 'investigation_wave', 'investigation_candidate',
    'investigation_candidate_assessment', 'investigation_phase_checkpoint',
    'investigation_checkpoint_candidate', 'investigation_candidate_release'
  ] loop
    if not has_table_privilege('carr_writer', v_table, 'select')
       or not has_table_privilege('carr_writer', v_table, 'insert') then
      raise exception '0116: carr_writer needs select and insert on %', v_table;
    end if;
    if has_table_privilege('carr_writer', v_table, 'update')
       or has_table_privilege('carr_writer', v_table, 'delete') then
      raise exception '0116: carr_writer must not hold update or delete on append-only %', v_table;
    end if;
  end loop;
  if not has_table_privilege('carr_reader', 'v_investigation_coverage', 'select')
     or not has_table_privilege('carr_reader', 'v_investigation_candidate_latest_assessment', 'select') then
    raise exception '0116: carr_reader cannot inspect investigation coverage views';
  end if;
end;
$$;

commit;
